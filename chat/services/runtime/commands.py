"""Comandos de execução (M2): criação idempotente, retomada explícita.

Reutiliza `reserve_run` (idempotência + exclusividade da conversa) e só
muda o estado para `queued`: a pergunta é gravada uma vez, a configuração
nunca é reinterpretada ao repetir a requisição.
"""

from __future__ import annotations

from chat.models import GenerationRun
from chat.services.generation import reserve_run
from chat.services.runtime import journal

RESUMABLE_STATES = ("awaiting_approval", "interrupted")


def create_command(*, conversation, text: str, idempotency_key: str):
    """Reserva (ou recupera) o run e o enfileira. Retorna (run, created)."""
    run, created, _ = reserve_run(
        conversation=conversation, content=text, idempotency_key=idempotency_key
    )
    if created:
        GenerationRun.objects.filter(pk=run.pk).update(state="queued")
        run.refresh_from_db()
        journal.append(run, "run_queued", {"state": "queued", "attempt": run.attempt})
    return run, created


def resume(run: GenerationRun) -> GenerationRun:
    """Recoloca run retomável em `queued`. Fora disso: ValueError (vira 409)."""
    if run.state not in RESUMABLE_STATES:
        raise ValueError(f"Estado '{run.state}' não permite retomada.")
    GenerationRun.objects.filter(pk=run.pk).update(
        state="queued", cancel_requested=False, attempt=run.attempt + 1
    )
    run.refresh_from_db()
    journal.append(run, "run_resumed", {"state": "queued", "attempt": run.attempt})
    return run
