"""Delegação em Equipe (M4 / C-A3): `delegate_to_agent` + AgentRun filho.

Só o coordenador em modo Equipe. Limites duros: máx 2 filhas,
profundidade 1 (neto bloqueado), até 2 leituras paralelas, filhos só
leitura (sem escrita, sem nova delegação), orçamento global com ledger
(`budget.py`), cancelamento estruturado em cascata, checkpoints/retomada
sem repetir concluídas.

Sem chamadas pagas no núcleo: o turno do filho recebe um `runner`
injetável (testes usam fakes); o runner padrão reutiliza
`tools.chat_loop.run_tool_events` com catálogo só-leitura (import tardio,
sem ciclo).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from chat.services.agents import budget as _budget
from chat.services.tools.registry import ToolRecord

DELEGATE_STABLE_ID = "local:delegate_to_agent"

DELEGATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "specialist": {"type": "string"},
        "task": {"type": "string"},
        "completion_criteria": {"type": "string"},
        "references": {"type": "string"},
    },
    "required": ["specialist", "task"],
    "additionalProperties": False,
}

# Args que nunca cruzam para o filho (C-A3: sem código, prompt
# privilegiado, credencial, URL, permissões ou modelo).
FORBIDDEN_ARGS = ("code", "system_prompt", "credential", "url", "permissions", "model")

# Resultado validado do filho (C-A3).
CHILD_RESULT_FIELDS = ("state", "summary", "evidence", "limitations", "failure")


def delegate_record() -> ToolRecord:
    """Ferramenta interna: visível só ao coordenador em modo Equipe."""
    return ToolRecord(
        stable_id=DELEGATE_STABLE_ID,
        origin="local",
        original_name="delegate_to_agent",
        description="Delega uma subtarefa a um especialista (só coordenador, Equipe).",
        input_schema=DELEGATE_SCHEMA,
        version="m4",
        approval="auto",
    )


def validate_args(args: Any) -> str | None:
    """Schema + proibidos. Retorna erro ou None. Puro."""
    from chat.services.tools import executor as _executor

    err = _executor.validate_args(DELEGATE_SCHEMA, args)
    if err:
        return err
    if not (args.get("task") or "").strip():
        return "Campo 'task' deve ser texto não vazio."
    for key in FORBIDDEN_ARGS:
        if key in (args or {}):
            return f"Campo proibido em delegação: {key!r}."
    return None


def validate_delegation(
    *,
    mode: str,
    delegatable_ids: list[str],
    specialist_uuid: str | None,
    specialist_complete: bool,
    existing_children: int,
    max_child_runs: int,
    parent_depth: int,
    budget_summary: dict | None = None,
    elapsed_s: float = 0.0,
) -> str | None:
    """Regras de delegação. None = autorizada; senão o código. Pura."""
    if mode != "team":
        return "not_team"
    if not specialist_uuid or specialist_uuid not in (delegatable_ids or []):
        return "unknown_specialist"
    if not specialist_complete:
        return "specialist_incomplete"
    depth_err = _budget.check_depth(parent_depth)
    if depth_err:
        return depth_err
    count_err = _budget.check_child_count(existing_children, max_child_runs)
    if count_err:
        return count_err
    if budget_summary is not None and _budget.budget_exceeded(budget_summary):
        return _budget.budget_exceeded(budget_summary)
    if elapsed_s > _budget.ACTIVE_BUDGET_S:
        return "budget_time"
    return None


def child_catalog(parent_catalog: list) -> list:
    """Catálogo do filho: só leitura (approval auto), sem delegação. Puro."""
    return [
        r
        for r in parent_catalog or []
        if r.approval == "auto" and r.stable_id != DELEGATE_STABLE_ID
    ]


def validate_child_result(raw: Any) -> tuple[dict | None, str | None]:
    """Normaliza o retorno do filho {estado, síntese, evidências, limitações, falha}.

    Retorna (normalizado, None) ou (None, código). Puro; nunca levanta.
    """
    if not isinstance(raw, dict):
        return None, "invalid_child_result"
    state = raw.get("state") or raw.get("estado") or "done"
    if state not in ("done", "failed"):
        return None, "invalid_child_state"
    summary = raw.get("summary", raw.get("síntese", ""))
    if not isinstance(summary, str) or not summary.strip():
        return None, "empty_child_summary"
    evidence = raw.get("evidence", raw.get("evidências", []))
    if evidence is None:
        evidence = []
    if not isinstance(evidence, list):
        return None, "invalid_child_evidence"
    limitations = raw.get("limitations", raw.get("limitações", ""))
    failure = raw.get("failure", raw.get("falha", ""))
    return (
        {
            "state": state,
            "summary": summary.strip()[:4000],
            "evidence": evidence[:20],
            "limitations": (limitations or "")[:2000] if isinstance(limitations, str) else "",
            "failure": (failure or "")[:2000] if isinstance(failure, str) else "",
        },
        None,
    )


def child_answer_block(tool_use_id: str, result: dict, *, owner=None, conversation=None) -> dict:
    """tool_result do pai: produto + referências revalidadas como dado (nunca ordem).

    Com escopo (dono + conversa), só trechos resolvidos contra chunks
    autorizados viram citação; o resto é descartado, não repassado
    (M5/§6). Sem escopo, nada é apresentado como citação documental.
    """
    from chat.services.agents import evidence as _evidence
    from chat.services.agents import thinking as _agent_thinking

    content = _agent_thinking.child_answer_as_user_content(
        summary=result["summary"], limitations=result.get("limitations", "")
    )
    text = "\n".join(b.get("text", "") for b in content["content"])
    if owner is not None and conversation is not None:
        manifest = _evidence.revalidate(
            owner=owner,
            conversation=conversation,
            refs=result.get("evidence") or [],
        )
        for item in manifest["valid"]:
            text += (
                f"\nEvidência revalidada [{item['chunk_uuid'][:8]}] "
                f"{item['doc']}: {item['excerpt']}"
            )
        if manifest["dropped"]:
            text += (
                f"\n({len(manifest['dropped'])} referência(s) do especialista "
                "descartadas: sem chunk autorizado correspondente.)"
            )
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": text,
        "is_error": result["state"] != "done",
    }


def error_block(tool_use_id: str, code: str, message: str) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": f"Erro {code}: {message}",
        "is_error": True,
    }


# --- persistência (transações curtas) ---


def spawn_child(
    *,
    owner,
    conversation,
    parent_run,
    specialist_version,
    tool_use_id: str,
    task: str,
    completion_criteria: str = "",
    references: str = "",
) -> object:
    """Persiste AgentRun filho (depth 1) + Delegation + ledger + eventos."""
    from django.db import transaction

    from chat.models_agents import AgentRun, Delegation

    with transaction.atomic():
        child = AgentRun.objects.create(
            owner=owner,
            conversation=conversation,
            parent=parent_run,
            agent_version=specialist_version,
            depth=parent_run.depth + 1,
            task=task,
            completion_criteria=completion_criteria or "",
            state="running",
            snapshot={
                "specialist": specialist_version.definition.name,
                "revision": specialist_version.revision,
                "references": (references or "")[:2000],
            },
        )
        Delegation.objects.create(
            parent_run=parent_run,
            child_run=child,
            tool_use_id=tool_use_id,
            released_context={"task": task[:2000]},
        )
    root = parent_run if parent_run.parent_id is None else _root_of(parent_run)
    _budget.record(root_run=root, run=child, kind="child_slot", tokens=0, note=tool_use_id)
    _budget.log_event(run=parent_run, kind="child_spawned", payload={"child": str(child.uuid)})
    _budget.log_event(run=child, kind="child_started", payload={"task": task[:500]})
    return child


def _root_of(run):
    from chat.models_agents import AgentRun

    node = run
    while node.parent_id is not None:
        node = AgentRun.objects.get(pk=node.parent_id)
    return node


def finish_child(*, child, result: dict) -> None:
    """Persiste o retorno validado; atualiza checkpoint do pai."""
    from django.db import transaction

    with transaction.atomic():
        db_child = type(child).objects.get(pk=child.pk)
        db_child.state = "done" if result["state"] == "done" else "failed"
        db_child.result_summary = result["summary"]
        db_child.result_evidence = result["evidence"]
        db_child.limitations = result["limitations"]
        db_child.failure_reason = result["failure"]
        db_child.checkpoint = {"completed": True, "at": time.time()}
        db_child.save()
    _budget.log_event(run=child, kind="child_finished", payload={"state": result["state"]})


def checkpoint_data(parent_run) -> dict:
    """Filhas concluídas (para retomada sem repetir). Puro sobre linhas."""
    done = [str(c.uuid) for c in parent_run.children.all() if c.state in ("done", "failed")]
    return {"completed_child_uuids": done, "at": time.time()}


def pending_specs(specs: list[dict], completed_uuids: list[str]) -> list[dict]:
    """Specs ainda não concluídas (retomada). Puro."""
    done = set(completed_uuids or [])
    return [s for s in specs if str(s.get("child_uuid", "")) not in done]


# --- execução paralela (até 2 leituras simultâneas) ---


async def _gather_capped(
    tasks: list[Callable[[], Awaitable[Any]]], limit: int = _budget.MAX_PARALLEL
) -> list[Any]:
    """Concorrência limitada por semáforo. Puro (runner injetável)."""
    sem = asyncio.Semaphore(max(1, limit))
    results: list[Any] = [None] * len(tasks)

    async def _one(index: int, coro_factory):
        async with sem:
            results[index] = await coro_factory()

    await asyncio.gather(*[_one(i, factory) for i, factory in enumerate(tasks)])
    return results


async def run_children(
    specs: list[dict],
    runner: Callable[[dict], Awaitable[dict]],
    *,
    limit: int = _budget.MAX_PARALLEL,
) -> list[tuple[dict | None, str | None]]:
    """Executa filhas com no máx `limit` simultâneas. Cada item: (resultado, erro).

    `runner(spec)` é injetável (fakes nos testes; turno real em produção).
    Erro do runner vira ("None", "child_failed") — nunca levanta.
    """
    if not specs:
        return []

    async def _factory(spec):
        async def _run():
            try:
                raw = await runner(spec)
            except Exception:
                return None, "child_failed"
            return validate_child_result(raw)

        return _run

    factories = [await _factory(s) for s in specs]
    return await _gather_capped(factories, limit)


# --- handler para o loop de ferramentas do pai ---


def build_handler(
    *,
    owner,
    conversation,
    parent_run,
    coordinator_version,
    mode: str,
    parent_catalog: list,
    resolve_specialist,
    child_runner,
) -> dict:
    """Hook `delegate` para `chat_loop.run_tool_events` (só raiz Equipe).

    `resolve_specialist(uuid_str)` → AgentVersion|None (publicada/completa).
    `child_runner(spec)` → dict bruto do filho (default em `default_child_runner`).
    Retorna {"record", "handle"}; `handle(call)` → (events, block).
    """
    record = delegate_record()

    async def handle(call: dict) -> tuple[list[dict], dict]:
        from asgiref.sync import sync_to_async

        args = call.get("input") or {}
        err = validate_args(args)
        if err:
            return [], error_block(call["id"], "invalid_args", err)
        specialist_uuid = args.get("specialist")
        version = await sync_to_async(resolve_specialist)(specialist_uuid)
        existing = await sync_to_async(lambda: parent_run.children.count())()
        summary = await sync_to_async(_budget.totals_for)(_root_of_sync(parent_run))
        started = (parent_run.checkpoint or {}).get("started_at") or time.time()
        code = validate_delegation(
            mode=mode,
            delegatable_ids=list(coordinator_version.delegatable_ids or []),
            specialist_uuid=str(specialist_uuid),
            specialist_complete=version is not None,
            existing_children=existing,
            max_child_runs=coordinator_version.max_child_runs,
            parent_depth=parent_run.depth,
            budget_summary=summary,
            elapsed_s=time.time() - started,
        )
        if code:
            return [], error_block(call["id"], code, _explain(code))
        child = await sync_to_async(spawn_child)(
            owner=owner,
            conversation=conversation,
            parent_run=parent_run,
            specialist_version=version,
            tool_use_id=call["id"],
            task=args["task"].strip(),
            completion_criteria=(args.get("completion_criteria") or "").strip(),
            references=(args.get("references") or "").strip(),
        )
        spec = {
            "child_uuid": str(child.uuid),
            "task": args["task"].strip(),
            "catalog": child_catalog(parent_catalog),
        }
        outcomes = await run_children([spec], child_runner)
        result, out_err = outcomes[0]
        if out_err:
            result = {
                "state": "failed",
                "summary": "",
                "evidence": [],
                "limitations": "",
                "failure": out_err,
            }
            failed, _ = validate_child_result(
                {"state": "failed", "summary": "Filha falhou.", "failure": out_err}
            )
            await sync_to_async(finish_child)(child=child, result=failed)
            return [], error_block(call["id"], out_err, "Especialista falhou.")
        await sync_to_async(finish_child)(child=child, result=result)
        block = await sync_to_async(child_answer_block)(
            call["id"], result, owner=owner, conversation=conversation
        )
        return [], block

    return {"record": record, "handle": handle}


def _root_of_sync(run):
    return _root_of(run)


def _explain(code: str) -> str:
    return {
        "not_team": "Delegação só em modo Equipe.",
        "unknown_specialist": "Especialista fora dos delegáveis do coordenador.",
        "specialist_incomplete": "Especialista sem versão publicada completa.",
        "max_depth": "Profundidade máxima 1: filhas não delegam.",
        "max_children": "Máximo 2 filhas por coordenador.",
        "budget_generations": "Orçamento de gerações esgotado.",
        "budget_invocations": "Orçamento de invocações esgotado.",
        "budget_tokens": "Orçamento de tokens esgotado.",
        "budget_time": "Orçamento de tempo (180s) esgotado.",
    }.get(code, code)


async def default_child_runner(spec: dict, **ctx) -> dict:
    """Turno real do filho: loop de ferramentas só-leitura, sem delegação.

    `ctx`: client, model, system, max_tokens, owner, conversation, vision,
    cancel_flag. Reúso (não réplica): `run_tool_events` com catálogo
    filtrado; o filho nunca recebe o hook `delegate` → recursão
    estruturalmente bloqueada. Preenche `spec["stats"]` (model_calls,
    output_tokens) para o ledger do orçamento.
    """
    from chat.services.tools.chat_loop import LoopState, run_tool_events
    from chat.services.tools.context import ExecutionContext

    catalog = spec.get("catalog") or []
    state = LoopState(
        messages=[{"role": "user", "content": [{"type": "text", "text": spec["task"]}]}]
    )
    run_uuid = spec.get("child_uuid") or "child"
    execution_ctx = ExecutionContext(
        user_id=ctx["owner"].pk,
        conversation_id=ctx["conversation"].pk,
        run_uuid=f"child:{run_uuid}",
    )
    final_text, stopped = "", "end_turn"
    async for ev in run_tool_events(
        client=ctx["client"],
        model=ctx["model"],
        system=ctx.get("system") or "",
        max_tokens=ctx.get("max_tokens") or 1024,
        state=state,
        catalog=catalog,
        ctx=execution_ctx,
        owner=ctx["owner"],
        conversation=ctx["conversation"],
        run_uuid=f"child:{run_uuid}",
        live=False,
        vision=ctx.get("vision"),
        cancel_flag=ctx.get("cancel_flag"),
    ):
        if ev.get("type") == "turn_done":
            final_text = ev.get("final_text", "")
            stopped = ev.get("stopped", "end_turn")
    spec["stats"] = {
        "model_calls": state.model_calls,
        "output_tokens": state.usage.get("output_tokens"),
    }
    if stopped != "end_turn":
        return {"state": "failed", "summary": "Filha interrompida.", "failure": stopped}
    if not final_text.strip():
        return {"state": "failed", "summary": "", "failure": "empty"}
    return {"state": "done", "summary": final_text.strip(), "evidence": []}
