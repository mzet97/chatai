"""Claim de runs pelo worker local (M2, SQLite).

Compare-and-set (`UPDATE … WHERE state='queued'`), transação curta, sem
`select_for_update` (SQLite não bloqueia linha — ADR/state-machines.md).
Fencing token fica para M3/PostgreSQL; aqui `claimed_by` + heartbeat.
"""

from __future__ import annotations

from django.utils import timezone

from chat.models import TERMINAL_RUN_STATES, GenerationRun


def claim_next(worker_id: str) -> GenerationRun | None:
    """Reivindica o `queued` mais antigo; None quando não há elegível."""
    candidate = GenerationRun.objects.filter(state="queued").order_by("started_at").first()
    if candidate is None:
        return None
    taken = GenerationRun.objects.filter(pk=candidate.pk, state="queued").update(
        state="running",
        claimed_by=worker_id,
        last_heartbeat=timezone.now(),
    )
    if not taken:
        return None  # outro worker reivindicou primeiro
    candidate.refresh_from_db()
    return candidate


def heartbeat(run: GenerationRun) -> None:
    GenerationRun.objects.filter(pk=run.pk).update(last_heartbeat=timezone.now())


def is_terminal(state: str) -> bool:
    return state in TERMINAL_RUN_STATES
