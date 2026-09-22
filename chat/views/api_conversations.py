"""API de conversas: CRUD, busca paginada, mensagens, exportação."""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from chat.models import Conversation, GenerationRun
from chat.services import delivery, exports, thinking, variation
from chat.services.anthropic_client import resolve_for_user
from chat.views._body import MODEL_MAX, SYSTEM_PROMPT_MAX, TITLE_MAX, clean_str, parse_body
from chat.views._scoping import owned_conversation


def _temperature_support(user, conv) -> dict:
    """Estado de compatibilidade do nível para o modelo/base efetivos (detalhe)."""
    resolved, _ = resolve_for_user(user, conv)
    model = conv.preferred_model or resolved.model
    state, reason = variation.resolve_compatibility(model=model, base_url=resolved.base_url)
    return {"state": state, "reason": reason, "model": model}


def _thinking_support(user, conv) -> dict:
    """Capacidade de pensamento/visão para o modelo/base efetivos (detalhe)."""
    resolved, _ = resolve_for_user(user, conv)
    model = conv.preferred_model or resolved.model
    cap = thinking.capability_for(model=model, base_url=resolved.base_url)
    return {
        "capability": cap,
        "vision": thinking.vision_for(model=model, base_url=resolved.base_url),
        "model": model,
    }


def _agent_effective(conv) -> dict:
    """Valores efetivos do agente + origem por campo (M3/AG-1.3, visíveis).

    Sem agente aplicável: {"applied": False, "mode": ...}. Nunca levanta por
    perfil ausente/arquivado: o caminho Chat segue com applied=False.
    """
    from chat.services.agents import effective as _effective

    try:
        resolved = _effective.resolve_agent(conv)
    except Exception:
        return {"applied": False, "mode": conv.agent_mode or "chat"}
    if resolved is None:
        return {"applied": False, "mode": conv.agent_mode or "chat"}
    values, origins = resolved["values"], resolved["origins"]
    keys = (
        "model",
        "thinking_mode",
        "thinking_level",
        "thinking_budget",
        "variation_level",
        "cache_mode",
        "cache_ttl",
    )
    return {
        "applied": True,
        "mode": resolved["mode"],
        "definition": resolved["definition"].name,
        "version_revision": resolved["version"].revision,
        "values": {k: values[k] for k in keys},
        "origins": {k: origins[k] for k in keys},
    }


def _conv_json(c, *, with_aggregates=False):
    data = {
        "uuid": str(c.uuid),
        "title": c.title,
        "archived": c.archived,
        "preferred_model": c.preferred_model,
        "system_prompt": c.system_prompt,
        "temperature_level": c.temperature_level or "medium",
        "thinking_mode": c.thinking_mode or thinking.DEFAULT_MODE,
        "thinking_level": c.thinking_level or thinking.DEFAULT_LEVEL,
        "thinking_budget": c.thinking_budget or thinking.DEFAULT_BUDGET,
        "thinking_show_summary": bool(c.thinking_show_summary),
        "agent_mode": c.agent_mode or "chat",
        "agent_definition_uuid": str(c.agent_definition.uuid) if c.agent_definition_id else None,
        "cache_mode": c.cache_mode or "",
        "cache_ttl": c.cache_ttl or "",
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
        body, err = parse_body(request)
        if err is not None:
            return err
        title = clean_str(body.get("title") or "Nova conversa", TITLE_MAX)
        model = clean_str(body.get("preferred_model") or "", MODEL_MAX)
        prompt = clean_str(body.get("system_prompt") or "", SYSTEM_PROMPT_MAX)
        if title is None or model is None or prompt is None:
            return JsonResponse(
                {"code": "validation", "message": "Campos devem ser texto."}, status=400
            )
        conv = Conversation.objects.create(
            owner=request.user,
            title=title,
            preferred_model=model,
            system_prompt=prompt,
        )
        data = _conv_json(conv)
        data["temperature_support"] = _temperature_support(request.user, conv)
        data["thinking_support"] = _thinking_support(request.user, conv)
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
        data["thinking_support"] = _thinking_support(request.user, conv)
        data["agent_effective"] = _agent_effective(conv)
        return JsonResponse(data)
    if request.method == "DELETE":
        conv.delete()  # remove dados associados (sem promessa sobre cópias externas)
        return JsonResponse({"deleted": True})
    body, err = parse_body(request)
    if err is not None:
        return err
    limits = {"title": TITLE_MAX, "preferred_model": MODEL_MAX, "system_prompt": SYSTEM_PROMPT_MAX}
    for field, limit in limits.items():
        if field in body:
            cleaned = clean_str(body[field], limit)
            if cleaned is None:
                return JsonResponse(
                    {"code": "validation", "message": f"Campo '{field}' deve ser texto."},
                    status=400,
                )
            setattr(conv, field, cleaned)
    if "agent_mode" in body or "agent_definition_uuid" in body:
        if conv.active_run_id is not None:
            return JsonResponse(
                {
                    "code": "active_run",
                    "message": "Disponível após concluir ou interromper a resposta.",
                    "agent_mode": conv.agent_mode or "chat",
                },
                status=409,
            )
        from chat.views import api_agents as _api_agents

        selection, error = _api_agents.resolve_selection(request.user, body)
        if error is not None:
            return error
        _api_agents.apply_selection(conv, selection)
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
    for key, normalizer, attr in (
        ("thinking_mode", thinking.normalize_mode, "thinking_mode"),
        ("thinking_level", thinking.normalize_level, "thinking_level"),
        ("thinking_budget", thinking.normalize_budget, "thinking_budget"),
    ):
        if key in body:
            if conv.active_run_id is not None:
                return JsonResponse(
                    {
                        "code": "active_run",
                        "message": "Disponível após concluir ou interromper a resposta.",
                        key: getattr(conv, attr),
                    },
                    status=409,
                )
            try:
                setattr(conv, attr, normalizer(body[key]))
            except ValueError as exc:
                return JsonResponse({"code": "validation", "message": str(exc)}, status=400)
    if "thinking_show_summary" in body:
        if conv.active_run_id is not None:
            return JsonResponse(
                {
                    "code": "active_run",
                    "message": "Disponível após concluir ou interromper a resposta.",
                    "thinking_show_summary": bool(conv.thinking_show_summary),
                },
                status=409,
            )
        conv.thinking_show_summary = bool(body["thinking_show_summary"])
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
    # Cache (AG-5.1/M2): override da conversa; "" limpa (volta ao perfil).
    # Valores fora de disabled|stable|conversation e 5m|1h → 400, sem salvar.
    if "cache_mode" in body or "cache_ttl" in body:
        if conv.active_run_id is not None:
            return JsonResponse(
                {
                    "code": "active_run",
                    "message": "Disponível após concluir ou interromper a resposta.",
                    "cache_mode": conv.cache_mode or "",
                    "cache_ttl": conv.cache_ttl or "",
                },
                status=409,
            )
        from chat.services.agents import cache as _cache

        if "cache_mode" in body:
            raw = (body["cache_mode"] or "").strip().lower()
            if raw and raw not in _cache.MODES:
                return JsonResponse(
                    {
                        "code": "validation",
                        "message": "cache_mode: disabled, stable ou conversation.",
                    },
                    status=400,
                )
            conv.cache_mode = raw
        if "cache_ttl" in body:
            raw = (body["cache_ttl"] or "").strip().lower()
            if raw and raw not in _cache.TTLS:
                return JsonResponse(
                    {"code": "validation", "message": "cache_ttl: 5m ou 1h."},
                    status=400,
                )
            conv.cache_ttl = raw
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
    data["thinking_support"] = _thinking_support(request.user, conv)
    data["agent_effective"] = _agent_effective(conv)
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
    from chat.services.images import public_image

    results = []
    for m in page:
        images = [
            public_image(b)
            for b in (m.blocks or [])
            if isinstance(b, dict) and b.get("type") == "image"
        ]
        item = {
            "uuid": str(m.uuid),
            "seq": m.seq,
            "role": m.role,
            "text": m.text,
            "state": m.state,
            "run_id": run_by_answer.get(m.id),
            "created_at": m.created_at.isoformat(),
        }
        if images:
            item["images"] = images
        results.append(item)
    return JsonResponse(
        {
            "results": results,
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
