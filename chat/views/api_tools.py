"""Ferramentas por conversa + aprovações + continuação (M5).

Decisões só pelo endpoint protegido (sessão, CSRF, idempotência).
Texto no chat nunca aprova, executa ou retoma nada.
"""

import asyncio
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.http import require_http_methods

from chat.services.tools import approvals, catalog
from chat.views._scoping import owned_conversation, owned_run

# Seam de teste: mesma convenção de api_runs.CLIENT_FACTORY.
CLIENT_FACTORY = None


@login_required
def conversation_tools(request, conv_uuid):
    """GET prefs; PUT valida stable_ids contra o catálogo conhecido."""
    conv = owned_conversation(request.user, conv_uuid)
    if request.method == "GET":
        return JsonResponse(
            {
                "enabled": catalog.get_enabled(conv),
                "available": len(catalog.all_available(conv)),
            }
        )
    if request.method == "PUT":
        try:
            body = json.loads(request.body or "{}")
        except ValueError:
            return JsonResponse(
                {"code": "validation", "message": "JSON inválido."}, status=400
            )
        enabled = body.get("enabled", [])
        if not isinstance(enabled, list) or not all(
            isinstance(s, str) for s in enabled
        ):
            return JsonResponse(
                {"code": "validation", "message": "enabled deve ser lista de textos."},
                status=400,
            )
        try:
            saved = catalog.set_enabled(conv, enabled)
        except ValueError as exc:
            return JsonResponse({"code": "validation", "message": str(exc)}, status=400)
        return JsonResponse({"enabled": saved})
    return JsonResponse({"code": "method", "message": "Método não permitido."}, status=405)


@login_required
@require_http_methods(["GET"])
def conversation_tools_catalog(request, conv_uuid):
    """Catálogo selecionável, agrupado por origem (sem segredos)."""
    conv = owned_conversation(request.user, conv_uuid)
    groups: dict[str, list] = {}
    for rec in catalog.all_available(conv):
        groups.setdefault(rec.origin, []).append(
            {
                "stable_id": rec.stable_id,
                "name": rec.anthropic_name,
                "description": rec.description,
                "approval": rec.approval,
            }
        )
    return JsonResponse({"enabled": catalog.get_enabled(conv), "groups": groups})


@login_required
@require_http_methods(["POST"])
def approval_decide(request, run_uuid, approval_id):
    """Aprovar uma vez / recusar. CSRF + sessão via Django; idempotência por chave."""
    from chat.models_tools import ToolApproval

    run = owned_run(request.user, run_uuid)
    try:
        body = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"code": "validation", "message": "JSON inválido."}, status=400)
    decision = body.get("decision")
    key = (body.get("idempotency_key") or "").strip() or None
    try:
        row = ToolApproval.objects.get(public_id=approval_id, owner=request.user)
    except ToolApproval.DoesNotExist:
        return JsonResponse(
            {"code": "not_found", "message": "Aprovação não encontrada."}, status=404
        )
    if row.conversation_id != run.conversation_id or row.run_uuid != str(run.uuid):
        return JsonResponse(
            {"code": "forbidden", "message": "Aprovação de outra execução."}, status=403
        )
    try:
        decided = approvals.decide(approval_id, decision, request.user, idempotency_key=key)
    except ValueError:
        return JsonResponse(
            {"code": "validation", "message": "decision deve ser approve ou deny."},
            status=400,
        )
    except approvals.NotFound:
        return JsonResponse(
            {"code": "not_found", "message": "Aprovação não encontrada."}, status=404
        )
    except approvals.Expired:
        return JsonResponse(
            {"code": "expired", "message": "Aprovação expirada; solicite nova decisão."},
            status=410,
        )
    except approvals.AlreadyConsumed:
        return JsonResponse(
            {"code": "conflict", "message": "Aprovação já consumida."}, status=409
        )
    return JsonResponse(decided)


@login_required
@require_http_methods(["POST"])
async def run_continue(request, run_uuid):
    """Retoma execução pausada no mesmo transporte SSE (sem re-perguntar)."""
    from asgiref.sync import sync_to_async
    from django.contrib.auth.models import AnonymousUser

    from chat.services.generation import resume_run

    def _concrete_user():
        u = request.user
        _ = u.pk
        return u

    user = await sync_to_async(_concrete_user)()
    if isinstance(user, AnonymousUser):
        return JsonResponse({"code": "forbidden", "message": "Login exigido."}, status=403)

    def _get_run():
        from chat.models import GenerationRun

        try:
            return GenerationRun.objects.select_related("conversation").get(
                uuid=run_uuid, conversation__owner=user
            )
        except GenerationRun.DoesNotExist:
            return None

    run = await sync_to_async(_get_run)()
    if run is None:
        return JsonResponse(
            {"code": "not_found", "message": "Execução não encontrada."}, status=404
        )
    if run.state != "awaiting_approval":
        return JsonResponse(
            {"code": "validation", "message": "Execução não está pausada."}, status=400
        )

    async def event_source():
        yield ": connected\n\n"
        client = CLIENT_FACTORY(user, run) if CLIENT_FACTORY else None
        agen = resume_run(str(run.uuid), user=user, client=client)
        pending = asyncio.ensure_future(agen.__anext__())
        try:
            while True:
                done, _ = await asyncio.wait({pending}, timeout=15)
                if not done:
                    yield ": ping\n\n"
                    continue
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    return
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                pending = asyncio.ensure_future(agen.__anext__())
        finally:
            if not pending.done():
                pending.cancel()
            await agen.aclose()

    return StreamingHttpResponse(event_source(), content_type="text/event-stream; charset=utf-8")
