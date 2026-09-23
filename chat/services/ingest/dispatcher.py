"""M4: dispatcher da outbox (§15).

Reivindica mensagens `pending` (CAS, sem select_for_update no SQLite),
publica com confirm e só então marca `delivered`. Falha entre publish e
marcação deixa `pending` para redispatch — o consumidor tolera a
duplicata. `delivering` sem progresso volta à fila pelo `updated_at`.
O processamento pesado fica no consumidor/worker, não aqui.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from chat.models_rag import OutboxMessage
from chat.services.ingest.broker import Broker
from chat.services.ingest.consumer import handle_ingest_requested

STALE_DELIVERING_MINUTES = 5


def dispatch_pending(
    *,
    broker: Broker,
    dispatcher_id: str = "dispatch-1",
    limit: int = 50,
    consume: bool = True,
) -> dict:
    delivered = failed = 0
    attempted: set[int] = set()  # falha nesta passada não gira na mesma msg
    for _ in range(max(limit, 0)):
        msg = _claim_next(dispatcher_id, skip=attempted)
        if msg is None:
            break
        attempted.add(msg.pk)
        try:
            broker.publish(msg.topic, msg.payload)
        except Exception as exc:
            _release(msg, str(exc)[:200])
            msg.refresh_from_db()
            if msg.state == "failed":
                failed += 1
            continue
        _mark_delivered(msg)
        delivered += 1
        if consume and msg.topic == "ingest.requested":
            # Entrega confirmada ≠ processamento comprovado: falha aqui
            # não desfaz a entrega; o job segue na fila do rag_worker.
            try:
                handle_ingest_requested(msg.payload)
            except Exception:
                pass
    remaining = OutboxMessage.objects.filter(state__in=("pending", "delivering")).count()
    return {"delivered": delivered, "failed": failed, "remaining": remaining}


def _claim_next(dispatcher_id: str, skip: set[int] | None = None) -> OutboxMessage | None:
    now = timezone.now()
    stale = now - timedelta(minutes=STALE_DELIVERING_MINUTES)
    with transaction.atomic():
        pks = list(
            OutboxMessage.objects.filter(state="pending")
            .exclude(pk__in=skip or ())
            .order_by("created_at")
            .values_list("pk", flat=True)[:1]
        )
        if not pks:
            pks = list(
                OutboxMessage.objects.filter(state="delivering", updated_at__lt=stale)
                .exclude(pk__in=skip or ())
                .order_by("updated_at")
                .values_list("pk", flat=True)[:1]
            )
        if not pks:
            return None
        n = OutboxMessage.objects.filter(pk=pks[0], state__in=("pending", "delivering")).update(
            state="delivering",
            claimed_by=dispatcher_id[:120],
            updated_at=now,
        )
        if not n:
            return None
        return OutboxMessage.objects.get(pk=pks[0])


def _release(msg: OutboxMessage, error: str) -> None:
    msg.attempts += 1
    if msg.attempts >= OutboxMessage.MAX_ATTEMPTS:
        msg.state = "failed"
    else:
        msg.state = "pending"
    msg.error = error
    msg.save(update_fields=["attempts", "state", "error", "updated_at"])


def _mark_delivered(msg: OutboxMessage) -> None:
    msg.state = "delivered"
    msg.error = ""
    msg.delivered_at = timezone.now()
    msg.save(update_fields=["state", "error", "delivered_at", "updated_at"])
