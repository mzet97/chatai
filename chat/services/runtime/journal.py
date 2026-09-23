"""Diário de eventos da raiz (M2): append atômico com seq, leitura com cursor.

Só projeção liberada (sem segredos/assinaturas/contextos). Texto chega em
micro-lotes do gerador; aqui cada evento confirmado vira uma linha — nunca
commit por token do provedor (o lote já vem formado de `execute_run`).
"""

from __future__ import annotations

from django.db import transaction

from chat.models_runtime import RunJournalEvent

MAX_PAYLOAD_KEYS = 32


def _sanitize(payload: dict) -> dict:
    """Projeção liberada: chaves conhecidas, sem segredos."""
    if not isinstance(payload, dict):
        return {}
    clean = {k: v for k, v in list(payload.items())[:MAX_PAYLOAD_KEYS]}
    for banned in ("api_key", "secret", "signature", "base64", "credential"):
        clean.pop(banned, None)
    return clean


def append(run, kind: str, payload: dict | None = None) -> RunJournalEvent:
    """Acrescenta evento com a próxima seq (transação curta)."""
    with transaction.atomic():
        last = (
            RunJournalEvent.objects.filter(run=run)
            .order_by("-seq")
            .values_list("seq", flat=True)
            .first()
        )
        return RunJournalEvent.objects.create(
            run=run, seq=(last or 0) + 1, kind=kind, payload=_sanitize(payload or {})
        )


def read(run, after: int = 0, limit: int = 200) -> list[dict]:
    """Eventos com seq > after (cursor do SSE; reconexão nunca reexecuta)."""
    rows = RunJournalEvent.objects.filter(run=run, seq__gt=after).order_by("seq")[:limit]
    return [
        {"seq": r.seq, "kind": r.kind, "payload": r.payload, "run_id": str(run.uuid)} for r in rows
    ]
