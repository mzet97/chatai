"""Orçamento global da árvore de agentes (M4 / C-A4).

Tetos (árvore inteira, raiz + filhas):
- 10 gerações (chamadas de modelo), 12 invocações de ferramenta,
  2 filhas, profundidade 1, 2 simultâneas, 12.000 tokens de saída, 180 s ativas.

Sem Django no núcleo puro (`check_*`, `summarize`): testável sem banco.
A persistência (`BudgetLedger`, `RunEvent`, `AgentRun.state`) vive nas
funções `record_*`/`cancel_tree`/`log_event` (transações curtas).
Cancelar a raiz fecha tudo (sem rollback do já consumido).
"""

from __future__ import annotations

MAX_GENERATIONS = 10
MAX_INVOCATIONS = 12
MAX_CHILD_RUNS = 2
MAX_DEPTH = 1
MAX_PARALLEL = 2
MAX_OUTPUT_TOKENS = 12000
ACTIVE_BUDGET_S = 180

LEDGER_KINDS = (
    "reserve_output",
    "usage",
    "cache_write",
    "cache_read",
    "child_slot",
    "call_slot",
)


def check_depth(parent_depth: int) -> str | None:
    """None = pode delegar; senão o código do bloqueio (neto = 409)."""
    if parent_depth + 1 > MAX_DEPTH:
        return "max_depth"
    return None


def check_child_count(existing: int, limit: int = MAX_CHILD_RUNS) -> str | None:
    """None = há slot de filha; senão `max_children`."""
    cap = min(limit, MAX_CHILD_RUNS)
    if existing >= cap:
        return "max_children"
    return None


def summarize(lines: list[dict]) -> dict:
    """Totais por kind a partir de linhas {kind, tokens}. Puro."""
    totals = {kind: 0 for kind in LEDGER_KINDS}
    for line in lines or []:
        kind = (line or {}).get("kind")
        if kind in totals:
            totals[kind] += (line or {}).get("tokens") or 0
    return {
        "generations": totals["call_slot"],
        "invocations": totals["usage"],
        "child_slots": totals["child_slot"],
        "output_tokens_reserved": totals["reserve_output"],
        "output_tokens_used": totals["usage"],
        "cache_write_tokens": totals["cache_write"],
        "cache_read_tokens": totals["cache_read"],
    }


def budget_exceeded(summary: dict) -> str | None:
    """Código do estouro, ou None quando há saldo."""
    if summary.get("generations", 0) >= MAX_GENERATIONS:
        return "budget_generations"
    if summary.get("invocations", 0) >= MAX_INVOCATIONS:
        return "budget_invocations"
    if summary.get("output_tokens_used", 0) >= MAX_OUTPUT_TOKENS:
        return "budget_tokens"
    return None


def _root_of(run):
    from chat.models_agents import AgentRun

    node = run
    while node.parent_id is not None:
        node = AgentRun.objects.get(pk=node.parent_id)
    return node


def totals_for(root_run) -> dict:
    """Totais da árvore a partir do ledger persistido."""
    from chat.models_agents import BudgetLedger

    lines = BudgetLedger.objects.filter(root_run=root_run).values("kind", "tokens")
    return summarize([{"kind": r["kind"], "tokens": r["tokens"]} for r in lines])


def record(*, root_run, run, kind: str, tokens: int = 0, note: str = "") -> None:
    """Uma linha de ledger (transação curta). Kind fora do contrato = ValueError."""
    from django.db import transaction

    from chat.models_agents import BudgetLedger

    if kind not in LEDGER_KINDS:
        raise ValueError(f"Kind de ledger inválido: {kind!r}.")
    with transaction.atomic():
        BudgetLedger.objects.create(
            root_run=root_run, run=run, kind=kind, tokens=tokens or 0, note=note[:300]
        )


def log_event(*, run, kind: str, payload: dict | None = None) -> None:
    """Trilha sequenciada (RunEvent seq monotônica por run)."""
    from django.db import transaction

    from chat.models_agents import RunEvent

    with transaction.atomic():
        last = RunEvent.objects.filter(run=run).order_by("-seq").first()
        seq = (last.seq + 1) if last else 1
        RunEvent.objects.create(run=run, seq=seq, kind=kind, payload=payload or {})


TERMINAL_STATES = ("done", "failed", "cancelled", "budget_exhausted")


def cancel_tree(root_run, *, reason: str = "cancelled") -> int:
    """Cancela a raiz e todas as filhas não terminais. Retorna nº de runs fechados."""
    from django.db import transaction

    from chat.models_agents import AgentRun

    closed = 0
    with transaction.atomic():
        runs = [root_run, *AgentRun.objects.filter(parent=root_run)]
        for run in runs:
            db_run = AgentRun.objects.get(pk=run.pk)
            if db_run.state in TERMINAL_STATES:
                continue
            db_run.state = "cancelled"
            db_run.failure_reason = (reason or "cancelled")[:500]
            db_run.save(update_fields=["state", "failure_reason", "updated_at"])
            closed += 1
    for run in [root_run, *root_run.children.all()]:
        try:
            log_event(run=run, kind="cancelled", payload={"reason": reason})
        except Exception:
            pass
    return closed
