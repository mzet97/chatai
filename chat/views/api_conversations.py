"""API de conversas: CRUD, busca paginada, mensagens, exportação."""

import json

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from chat.models import Conversation, GenerationRun
from chat.services import delivery, exports, variation
from chat.services.anthropic_client import resolve_for_user
from chat.views._scoping import owned_conversation


def _temperature_support(user, conv) -> dict:
    """Estado de compatibilidade do nível para o modelo/base efetivos (detalhe)."""
    resolved, _ = resolve_for_user(user, conv)
    model = conv.preferred_model or resolved.model
    state, reason = variation.resolve_compatibility(model=model, base_url=resolved.base_url)
    return {"state": state, "reason": reason, "model": model}


def _conv_json(c, *, with_aggregates=False):
    data = {
        "uuid": str(c.uuid),
        "title": c.title,
        "archived": c.archived,
        "preferred_model": c.preferred_model,
        "system_prompt": c.system_prompt,
        "temperature_level": c.temperature_level or "medium",
        "response_mode": c.response_mode or delivery.STREAMING,
        "max_output_tokens": c.max_output_tokens,
        "input_budget": c.input_budget,
        "strict_mode": c.strict_mode,
        "has_active_run": c.active_run_id is not None,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
    }
    if with_aggregates:
        agg = c.runs.aggregate(Sum("input_tokens"), Sum("output_tokens"))
        incomplete = c.runs.filter(input_tokens__isnull=True, state="done").exists()
        data["tokens"] = {
            "input": agg["input_tokens__sum"],
            "output": agg["output_tokens__sum"],
            "incomplete": incomplete,
        }
    return data


@login_required
@require_http_methods(["GET", "POST"])
def conversations(request):
    if request.method == "POST":
        body = json.loads(request.body or "{}")
        conv = Conversation.objects.create(
            owner=request.user,
            title=body.get("title") or "Nova conversa",
            preferred_model=body.get("preferred_model") or "",
            system_prompt=body.get("system_prompt") or "",
        )
        data = _conv_json(conv)
        data["temperature_support"] = _temperature_support(request.user, conv)
        return JsonResponse(data, status=201)
    q = (request.GET.get("q") or "").strip()
    qs = request.user.conversations.all()
    if (request.GET.get("archived") or "") != "1":
        qs = qs.filter(archived=False)
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(messages__text__icontains=q)).distinct()
    page = Paginator(qs.order_by("-updated_at"), 20).get_page(request.GET.get("page") or 1)
    return JsonResponse(
        {
            "results": [_conv_json(c) for c in page],
            "page": page.number,
            "num_pages": page.paginator.num_pages,
            "count": page.paginator.count,
        }
    )


@login_required
@require_http_methods(["GET", "PATCH", "DELETE"])
def conversation_detail(request, conv_uuid):
    conv = owned_conversation(request.user, conv_uuid)
    if request.method == "GET":
        data = _conv_json(conv, with_aggregates=True)
        data["temperature_support"] = _temperature_support(request.user, conv)
        return JsonResponse(data)
    if request.method == "DELETE":
        conv.delete()  # remove dados associados (sem promessa sobre cópias externas)
        return JsonResponse({"deleted": True})
    body = json.loads(request.body or "{}")
    for field in ("title", "preferred_model", "system_prompt"):
        if field in body:
            setattr(conv, field, body[field])
    if "temperature" in body:
        # O cliente envia nível, nunca número: recusa explícita, sem mass assignment.
        return JsonResponse(
            {"code": "validation", "message": "Use 'temperature_level' (low, medium ou high)."},
            status=400,
        )
    if "temperature_level" in body:
        if conv.active_run_id is not None:
            return JsonResponse(
                {
                    "code": "active_run",
                    "message": "Disponível após concluir ou interromper a resposta.",
                    "temperature_level": conv.temperature_level or "medium",
                },
                status=409,
            )
        try:
            conv.temperature_level = variation.normalize_level(body["temperature_level"])
        except ValueError as exc:
            return JsonResponse({"code": "validation", "message": str(exc)}, status=400)
    if "response_mode" in body:
        if conv.active_run_id is not None:
            return JsonResponse(
                {
                    "code": "active_run",
                    "message": "Disponível após concluir ou interromper a resposta.",
                    "response_mode": conv.response_mode or delivery.STREAMING,
                },
                status=409,
            )
        try:
            conv.response_mode = delivery.normalize_mode(body["response_mode"])
        except ValueError as exc:
            return JsonResponse({"code": "validation", "message": str(exc)}, status=400)
    if "archived" in body:
        conv.archived = bool(body["archived"])
    if "max_output_tokens" in body:
        conv.max_output_tokens = body["max_output_tokens"]
    if "input_budget" in body:
        conv.input_budget = body["input_budget"]
    if "strict_mode" in body:
        conv.strict_mode = bool(body["strict_mode"])
    conv.save()
    data = _conv_json(conv)
    data["temperature_support"] = _temperature_support(request.user, conv)
    return JsonResponse(data)


@login_required
@require_http_methods(["GET"])
def conversation_messages(request, conv_uuid):
    conv = owned_conversation(request.user, conv_uuid)
    page = Paginator(conv.messages.order_by("seq"), 200).get_page(request.GET.get("page") or 1)
    # Mapeia resposta → execução mais recente (para o botão "Detalhes" no histórico).
    run_by_answer = {}
    assistant_ids = [m.id for m in page if m.role == "assistant"]
    if assistant_ids:
        for run in GenerationRun.objects.filter(
            conversation=conv, assistant_message_id__in=assistant_ids
        ).order_by("started_at"):
            run_by_answer[run.assistant_message_id] = str(run.uuid)
    return JsonResponse(
        {
            "results": [
                {
                    "uuid": str(m.uuid),
                    "seq": m.seq,
                    "role": m.role,
                    "text": m.text,
                    "state": m.state,
                    "run_id": run_by_answer.get(m.id),
                    "created_at": m.created_at.isoformat(),
                }
                for m in page
            ],
            "page": page.number,
            "num_pages": page.paginator.num_pages,
        }
    )


@login_required
@require_http_methods(["GET"])
def conversation_export(request, conv_uuid):
    conv = owned_conversation(request.user, conv_uuid)
    messages = list(conv.messages.order_by("seq"))
    runs = list(conv.runs.order_by("started_at"))
    fmt = request.GET.get("format") or "json"
    if fmt == "markdown":
        from django.http import HttpResponse

        resp = HttpResponse(exports.export_markdown(conv, messages), content_type="text/markdown")
        resp["Content-Disposition"] = f'attachment; filename="chat-{conv.uuid}.md"'
        return resp
    return JsonResponse(exports.export_json(conv, messages, runs))
