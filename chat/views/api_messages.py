"""Envio (reserva) e retry explícito. A execução ocorre no GET /stream (SSE)."""

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from chat.services.generation import (
    IdempotencyConflict,
    RunBusy,
    reserve_run,
    retry_info,
)
from chat.views import api_conversations
from chat.views._scoping import owned_conversation


@login_required
@require_http_methods(["GET", "POST"])
def conversation_messages(request, conv_uuid):
    """Dispatcher: GET lista o histórico; POST reserva um envio (idempotente)."""
    if request.method == "POST":
        return send_message(request, conv_uuid)
    return api_conversations.conversation_messages(request, conv_uuid)


@login_required
@require_http_methods(["POST"])
def send_message(request, conv_uuid):
    conv = owned_conversation(request.user, conv_uuid)
    body = json.loads(request.body or "{}")
    content = (body.get("content") or "").strip()
    key = (body.get("idempotency_key") or "").strip()
    if not content:
        return JsonResponse({"code": "validation", "message": "Conteúdo vazio."}, status=400)
    if len(content) > 100_000:
        return JsonResponse(
            {"code": "validation", "message": "Mensagem excede 100 mil caracteres."}, status=400
        )
    if not key:
        return JsonResponse(
            {"code": "validation", "message": "idempotency_key obrigatória."}, status=400
        )
    try:
        run, created, _ = reserve_run(conversation=conv, content=content, idempotency_key=key)
    except IdempotencyConflict:
        return JsonResponse(
            {"code": "conflict", "message": "Mesma chave com conteúdo diferente."}, status=409
        )
    except RunBusy as exc:
        return JsonResponse({"code": "run_busy", "message": str(exc)}, status=409)
    return JsonResponse(
        {
            "run_id": str(run.uuid),
            "user_message_id": str(run.user_message.uuid),
            "created": created,
            "stream_url": f"/api/runs/{run.uuid}/stream",
        },
        status=201 if created else 200,
    )


@login_required
@require_http_methods(["POST"])
def retry_message(request, conv_uuid, msg_uuid):
    conv = owned_conversation(request.user, conv_uuid)
    msg = conv.messages.filter(uuid=msg_uuid, role="user").first()
    if msg is None:
        return JsonResponse(
            {"code": "not_found", "message": "Mensagem não encontrada."}, status=404
        )
    ok, reason = retry_info(conversation=conv, user_message=msg)
    if not ok:
        return JsonResponse({"code": "validation", "message": reason}, status=400)
    body = json.loads(request.body or "{}")
    key = (body.get("idempotency_key") or "").strip()
    if not key:
        return JsonResponse(
            {"code": "validation", "message": "idempotency_key obrigatória."}, status=400
        )
    try:
        run, created, _ = reserve_run(
            conversation=conv, content=msg.text, idempotency_key=key, user_message=msg
        )
    except (IdempotencyConflict, RunBusy) as exc:
        code = "conflict" if isinstance(exc, IdempotencyConflict) else "run_busy"
        return JsonResponse({"code": code, "message": str(exc)}, status=409)
    return JsonResponse(
        {
            "run_id": str(run.uuid),
            "attempt": run.attempt,
            "stream_url": f"/api/runs/{run.uuid}/stream",
        },
        status=201 if created else 200,
    )
