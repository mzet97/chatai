"""Execução SSE, cancelamento e inspeção de runs (RF-10/11/12)."""

import asyncio
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.http import require_http_methods

from chat.services.generation import execute_run, request_cancel
from chat.views._scoping import owned_run

# Seam de teste: fábrica opcional de cliente falso. Produção usa sempre o SDK real.
CLIENT_FACTORY = None

# Comentário SSE enviado quando o provedor fica quieto (modo completo ou
# resposta longa). É um único consumidor com timeout — nunca dois leitores
# disputando o mesmo iterador.
HEARTBEAT_SECONDS = 15


def _sse_event(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


@login_required
async def run_stream(request, run_uuid):
    from asgiref.sync import sync_to_async
    from django.contrib.auth.models import AnonymousUser

    # request.user é lazy e bate no banco: avaliar dentro da thread.
    def _concrete_user():
        u = request.user
        _ = u.pk  # força a avaliação (DB) ainda no contexto síncrono
        return u

    user = await sync_to_async(_concrete_user)()
    if isinstance(user, AnonymousUser):
        return JsonResponse({"code": "forbidden", "message": "Login exigido."}, status=403)
    try:
        run = await _owned_run_async(user, run_uuid)
    except _NotFound:
        return JsonResponse(
            {"code": "not_found", "message": "Execução não encontrada."}, status=404
        )

    async def event_source():
        # : ping inicial mantém proxies acordados
        yield ": connected\n\n"
        client = CLIENT_FACTORY(user, run) if CLIENT_FACTORY else None
        agen = execute_run(str(run.uuid), user=user, client=client)
        # Um único consumidor: asyncio.wait com timeout NÃO cancela o __anext__
        # pendente (wait_for cancelaria e mataria a geração a cada heartbeat).
        pending = asyncio.ensure_future(agen.__anext__())
        try:
            while True:
                done, _ = await asyncio.wait({pending}, timeout=HEARTBEAT_SECONDS)
                if not done:
                    # Upstream quieto (modo completo): heartbeat não é texto.
                    yield ": ping\n\n"
                    continue
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    return
                yield _sse_event(event)
                pending = asyncio.ensure_future(agen.__anext__())
        finally:
            if not pending.done():
                pending.cancel()
            await agen.aclose()

    response = StreamingHttpResponse(
        event_source(), content_type="text/event-stream; charset=utf-8"
    )
    response["Cache-Control"] = "no-store, no-transform"
    response["X-Accel-Buffering"] = "no"
    return response


class _NotFound(Exception):
    pass


async def _owned_run_async(user, run_uuid):
    from asgiref.sync import sync_to_async

    from chat.models import GenerationRun

    def _get():
        try:
            return GenerationRun.objects.select_related("conversation").get(
                uuid=run_uuid, conversation__owner=user
            )
        except GenerationRun.DoesNotExist as exc:
            raise _NotFound() from exc

    return await sync_to_async(_get)()


@login_required
@require_http_methods(["POST"])
def run_cancel(request, run_uuid):
    run = owned_run(request.user, run_uuid)
    if request_cancel(str(run.uuid)):
        return JsonResponse({"cancelled": True})
    return JsonResponse(
        {"code": "validation", "message": "Execução já terminou ou não está ativa."}, status=400
    )


@login_required
@require_http_methods(["GET"])
def run_detail(request, run_uuid):
    """Painel didático (RF-10): snapshot da execução, sem segredos e sem auth headers."""
    run = owned_run(request.user, run_uuid)
    snap = run.snapshot or {}
    snap.pop("api_key", None)
    tools = _tools_panel(run)
    return JsonResponse(
        {
            "uuid": str(run.uuid),
            "state": run.state,
            "attempt": run.attempt,
            "requested_model": run.requested_model,
            "actual_model": run.actual_model,
            "request_id": run.request_id,
            "response_id": run.response_id,
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "stop_reason": run.stop_reason,
            "truncated": run.truncated,
            "error_code": run.error_code,
            "error_message": run.error_message,
            "effective_response_mode": snap.get("effective_response_mode"),
            "requested_response_mode": snap.get("requested_response_mode"),
            "snapshot": snap,  # config imutável da chamada (system, orçamento, origens)
            "context_used": run.context_used,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "tools": tools,
        }
    )


def _tools_panel(run) -> dict:
    """Painel didático M5: etapas, invocações e aprovações — sem segredos,
    sem dumps brutos, sem args completos além do preview já sanitizado."""
    from django.utils import timezone

    from chat.models_tools import ModelStep, ToolApproval, ToolInvocation

    now = timezone.now()

    steps = list(
        ModelStep.objects.filter(run_uuid=str(run.uuid))
        .order_by("step")
        .values("step", "model", "stop_reason", "input_tokens", "output_tokens", "tool_use_ids")
    )
    invocations = [
        {
            "step": i.step,
            "tool_use_id": i.tool_use_id,
            "name": i.anthropic_name,
            "decision": i.decision,
            "state": i.state,
            "effect": i.effect,
            "ok": i.result_ok,
            "error": i.error_code,
        }
        for i in ToolInvocation.objects.filter(
            conversation_id=run.conversation_id, run_uuid=str(run.uuid)
        ).order_by("step", "id")
    ]
    approvals = [
        {
            "approval_id": str(a.public_id),
            "tool_use_id": a.tool_use_id,
            "name": a.anthropic_name,
            "decision": a.decision,
            "preview": a.args_preview,
            "expired": a.expires_at <= now,
            "consumed": a.consumed_at is not None,
        }
        for a in ToolApproval.objects.filter(
            conversation_id=run.conversation_id, run_uuid=str(run.uuid)
        ).order_by("created_at")
    ]
    return {
        "enabled": (run.snapshot or {}).get("tools_enabled", []),
        "steps": steps,
        "invocations": invocations,
        "approvals": approvals,
    }
