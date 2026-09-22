"""Turno com ferramentas dirigido a SSE (M5).

Etapas `messages.create` (não-stream por etapa): nunca executamos de JSON
parcial por construção. Autorização em duas fases por etapa: primeiro valida
tudo e abre as aprovações necessárias; só executa o grupo sem pendência.
Pausa persiste o estado no snapshot do run; `resume` revalida prefs,
catálogo, schema e aprovação antes de qualquer efeito.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from chat.services.tools import executor as _executor
from chat.services.tools import limits
from chat.services.tools.context import ExecutionContext
from chat.services.tools.registry import ToolRecord, build_payload


@dataclass
class LoopState:
    messages: list[dict] = field(default_factory=list)
    step: int = 0
    model_calls: int = 0
    invocations: int = 0
    usage: dict = field(default_factory=lambda: {"input_tokens": None, "output_tokens": None})
    text_parts: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)

    def to_snapshot(self) -> dict:
        return {
            "messages": self.messages,
            "step": self.step,
            "model_calls": self.model_calls,
            "invocations": self.invocations,
            "usage": self.usage,
            "text_parts": self.text_parts,
        }

    @classmethod
    def from_snapshot(cls, snap: dict) -> LoopState:
        return cls(
            messages=[dict(m) for m in snap.get("messages", [])],
            step=int(snap.get("step", 0)),
            model_calls=int(snap.get("model_calls", 0)),
            invocations=int(snap.get("invocations", 0)),
            usage=dict(snap.get("usage", {"input_tokens": None, "output_tokens": None})),
            text_parts=list(snap.get("text_parts", [])),
        )


def parse_blocks(message) -> list[dict]:
    blocks = []
    for block in getattr(message, "content", None) or []:
        btype = getattr(block, "type", "")
        if btype == "text":
            text_block = {"type": "text", "text": getattr(block, "text", "")}
            cites = getattr(block, "citations", None)
            if cites:
                text_block["citations"] = cites
            blocks.append(text_block)
        elif btype == "tool_use":
            blocks.append(
                {
                    "type": "tool_use",
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "input": getattr(block, "input", None) or {},
                }
            )
    return blocks


def error_result(call_id: str, code: str, message: str) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": f"Erro {code}: {message}",
        "is_error": True,
    }


def _unexpected_approval(outcome: dict) -> dict:
    """Executor fora do contrato p/ aprovação: vira erro classificável, nunca assert."""
    if isinstance(outcome, dict) and outcome.get("error"):
        return outcome
    return {
        "ok": False,
        "error": {"code": "validation", "message": "Resposta inesperada do executor."},
    }


def ok_result(call_id: str, text: str, images: list[dict] | None = None) -> dict:
    """tool_result textual (legado) ou com blocos de imagem (M4/TV-5.2)."""
    from chat.services import images as _images

    return _images.tool_result_block(call_id, text, images or [])


VISION_UNSUPPORTED_MESSAGE = (
    "Modelo sem suporte a visão neste turno; resultado em imagem descartado. "
    "Troque o modelo ou repita sem anexos visuais."
)


def _save_step(run_uuid: str, step: int, message, tool_use_ids: list[str]) -> None:
    from django.db import transaction

    from chat.models_tools import ModelStep

    usage = getattr(message, "usage", None)
    from chat.services.agents import cache as _cache

    cache_metrics = _cache.parse_usage(usage)
    with transaction.atomic():
        ModelStep.objects.update_or_create(
            run_uuid=run_uuid,
            step=step,
            defaults={
                "model": getattr(message, "model", "") or "",
                "stop_reason": getattr(message, "stop_reason", "") or "",
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
                "cache_creation_input_tokens": cache_metrics["cache_creation_input_tokens"],
                "cache_read_input_tokens": cache_metrics["cache_read_input_tokens"],
                "cache_creation_detail": cache_metrics["cache_creation_detail"],
                "tool_use_ids": tool_use_ids,
            },
        )


def _save_invocation(
    *,
    owner,
    conversation,
    run_uuid: str,
    step: int,
    rec: ToolRecord | None,
    call: dict,
    decision: str,
    state: str,
    ok: bool | None = None,
    text: str = "",
    error_code: str | None = None,
    effect: str = "none",
) -> None:
    from django.db import transaction
    from django.utils import timezone

    from chat.models_tools import ToolInvocation

    with transaction.atomic():
        ToolInvocation.objects.update_or_create(
            conversation=conversation,
            run_uuid=run_uuid,
            tool_use_id=call["id"],
            defaults={
                "owner": owner,
                "step": step,
                "anthropic_name": call["name"],
                "args": call["input"] if isinstance(call["input"], dict) else {},
                "decision": decision,
                "state": state,
                "effect": effect,
                "result_ok": ok,
                "result_text": (text or "")[:40000],
                "error_code": error_code,
                "started_at": timezone.now(),
                "finished_at": timezone.now(),
            },
        )


async def execute_one(
    *,
    call: dict,
    rec: ToolRecord,
    approval: dict | None,
    ctx: ExecutionContext,
    catalog: list[ToolRecord],
    run_uuid: str,
    owner,
    conversation,
    step: int,
    vision: str | None = None,
) -> tuple[list[dict], dict]:
    """Executa UM item do grupo. Retorna (eventos, bloco_resultado).

    `vision`: capacidade do modelo efetivo; "no" descarta resultado em
    imagem (fail closed, sem bytes no estado) — M4/TV-4.4.
    """
    from asgiref.sync import sync_to_async

    events = [
        {
            "type": "tool_started",
            "step": step,
            "tool_use_id": call["id"],
            "name": call["name"],
        }
    ]
    outcome = await _executor.execute_authorized(
        call["name"],
        call["input"],
        ctx,
        tool_use_id=call["id"],
        approval=approval,
        records=catalog,
        run_uuid=run_uuid,
    )
    ok = bool(outcome.get("ok"))
    if ok:
        text = outcome["text"]
        result_images = outcome.get("images") or []
        if result_images and vision == "no":
            block = error_result(call["id"], "vision_unsupported", VISION_UNSUPPORTED_MESSAGE)
            ok = False
            err_code = "vision_unsupported"
            text = ""
            effect = "none"
            inv_state = "failed"
        else:
            block = ok_result(call["id"], text, result_images)
            effect = "done" if rec.approval == "require" else "none"
            err_code = None
            inv_state = "done"
    else:
        err = outcome["error"]
        block = error_result(call["id"], err["code"], err["message"])
        text = ""
        err_code = err["code"]
        effect = "unknown" if err["code"] == "timeout" else "none"
        inv_state = "refused" if err["code"] in ("denied", "refused") else "failed"
    await sync_to_async(_save_invocation)(
        owner=owner,
        conversation=conversation,
        run_uuid=run_uuid,
        step=step,
        rec=rec,
        call=call,
        decision="approve" if approval else "auto",
        state=inv_state,
        ok=ok,
        text=text,
        error_code=err_code,
        effect=effect,
    )
    events.append({"type": "tool_finished", "step": step, "tool_use_id": call["id"], "ok": ok})
    return events, block


def check_resume_entry(entry: dict, catalog: list[ToolRecord], owner, run_uuid: str):
    """Revalida item pausado. Retorna (rec, approval|None, erro|None)."""
    from chat.services.tools import approvals as _approvals

    rec = next((r for r in catalog if r.stable_id == entry.get("stable_id")), None)
    if rec is None or rec.version != entry.get("version"):
        return None, None, ("revoked", "Ferramenta revogada ou catálogo alterado.")
    args = entry.get("args", {})
    if _executor.validate_args(rec.input_schema, args):
        return None, None, ("invalid_args", "Argumentos inválidos no catálogo atual.")
    approval_id = entry.get("approval_id")
    if not approval_id:
        return rec, None, None  # auto adiada: executa na retomada
    try:
        approval = _approvals.load(approval_id)
    except _approvals.NotFound:
        return None, None, ("approval_missing", "Aprovação não encontrada.")
    if approval.get("decision") != "approve":
        return None, None, ("refused", "Uso recusado.")
    if approval.get("expired"):
        return None, None, ("expired", "Aprovação expirada.")
    if not _approvals.valid_for(rec, args, approval):
        return None, None, ("stale", "Aprovação não vale para a operação atual.")
    return rec, approval, None


async def run_tool_events(
    *,
    client,
    model: str,
    system: str,
    max_tokens: int,
    state: LoopState,
    catalog: list[ToolRecord],
    ctx: ExecutionContext,
    owner,
    conversation,
    run_uuid: str,
    live: bool,
    extra_body: dict | None = None,
    thinking: dict | None = None,
    output_config: dict | None = None,
    cancel_flag=None,
    vision: str | None = None,
    delegate=None,
):
    """Gerador de eventos finos. Terminal: `turn_done` ou `turn_paused`.

    `delegate` (M4/Equipe): {"record": ToolRecord, "handle": async fn} —
    chamadas ao coordenador interno `delegate_to_agent` desviam para
    `handle(call)` → (events, block). Filhas nunca recebem o hook
    (recursão estruturalmente bloqueada). None = caminho inalterado.
    """
    import asyncio

    from asgiref.sync import sync_to_async

    payload = build_payload(catalog)
    known = {r.anthropic_name: r for r in catalog}
    delegate_name = (delegate or {}).get("record")
    delegate_name = delegate_name.anthropic_name if delegate_name is not None else None

    while True:
        if state.model_calls >= limits.MAX_MODEL_STEPS:
            yield {"type": "turn_done", "stopped": "limit", "final_text": "".join(state.text_parts)}
            return
        if time.monotonic() - state.started_at > limits.ACTIVE_BUDGET_S:
            yield {
                "type": "turn_done",
                "stopped": "budget",
                "final_text": "".join(state.text_parts),
            }
            return
        if cancel_flag is not None and await cancel_flag():
            yield {
                "type": "turn_done",
                "stopped": "cancelled",
                "final_text": "".join(state.text_parts),
            }
            return
        yield {"type": "model_step_started", "step": state.step}
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [dict(m) for m in state.messages],
            "max_tokens": max_tokens,
        }
        if system:
            kwargs["system"] = [{"type": "text", "text": system}]
        kwargs.update(payload)
        if extra_body:
            kwargs["extra_body"] = extra_body
        if thinking is not None:
            kwargs["thinking"] = thinking
        if output_config is not None:
            kwargs["output_config"] = output_config
        message = await client.messages.create(**kwargs)
        state.model_calls += 1
        for key in ("input_tokens", "output_tokens"):
            value = getattr(getattr(message, "usage", None), key, None)
            if value is not None:
                state.usage[key] = value
        blocks = parse_blocks(message)
        tool_calls = [b for b in blocks if b["type"] == "tool_use"]
        tool_ids = [c["id"] for c in tool_calls]
        await sync_to_async(_save_step)(run_uuid, state.step, message, tool_ids)
        state.messages.append({"role": "assistant", "content": blocks})
        step_text = "".join(b.get("text", "") for b in blocks if b["type"] == "text")
        if step_text:
            state.text_parts.append(step_text)
            if live:
                yield {"type": "text_delta", "text": step_text}
        if not tool_calls or getattr(message, "stop_reason", "") != "tool_use":
            yield {
                "type": "turn_done",
                "stopped": "end_turn",
                "final_text": "".join(state.text_parts),
                "stop_reason": getattr(message, "stop_reason", ""),
                "usage": dict(state.usage),
                "final_blocks": blocks,
            }
            return
        # Fase 1: valida tudo, abre aprovações; nada executa ainda.
        deferred: list[tuple[dict, ToolRecord]] = []  # autos p/ fase 2
        delegated: list[dict] = []  # delegate_to_agent (M4) p/ fase 2 concorrente
        pending: list[tuple[dict, ToolRecord, str]] = []  # requires + approval_id
        error_blocks: list[dict] = []
        for call in tool_calls:
            yield {
                "type": "tool_call_requested",
                "step": state.step,
                "tool_use_id": call["id"],
                "name": call["name"],
            }
            rec = known.get(call["name"])
            if (
                delegate is not None
                and delegate_name is not None
                and call["name"] == delegate_name
                and isinstance(call["input"], dict)
            ):
                # M4: delegação interna desvia para o handler (fase 2
                # concorrente); schema inválido vira erro imediato.
                schema_err = _executor.validate_args(delegate["record"].input_schema, call["input"])
                if schema_err:
                    error_blocks.append(error_result(call["id"], "invalid_args", schema_err))
                    await sync_to_async(_save_invocation)(
                        owner=owner,
                        conversation=conversation,
                        run_uuid=run_uuid,
                        step=state.step,
                        rec=rec,
                        call=call,
                        decision="auto",
                        state="failed",
                        ok=False,
                        text="",
                        error_code="invalid_args",
                    )
                else:
                    delegated.append(call)
                continue
            if rec is None or not isinstance(call["input"], dict):
                outcome = _executor.unknown_tool(call["name"])
                bad = (call, rec, outcome)
            elif rec.approval == "deny":
                bad = (call, rec, _executor.denied(rec))
            else:
                schema_err = _executor.validate_args(rec.input_schema, call["input"])
                if schema_err:
                    bad = (
                        call,
                        rec,
                        {"ok": False, "error": {"code": "invalid_args", "message": schema_err}},
                    )
                elif rec.approval == "require":
                    outcome = await _executor.execute_authorized(
                        call["name"],
                        call["input"],
                        ctx,
                        tool_use_id=call["id"],
                        approval=None,
                        records=catalog,
                        run_uuid=run_uuid,
                    )
                    err_code = outcome.get("error", {}).get("code")
                    if err_code != "approval_required" or not outcome.get("approval_id"):
                        bad = (call, rec, _unexpected_approval(outcome))
                    else:
                        pending.append((call, rec, outcome["approval_id"]))
                        continue
                else:
                    deferred.append((call, rec))
                    continue
            call_b, rec_b, outcome_b = bad
            err = outcome_b["error"]
            error_blocks.append(error_result(call_b["id"], err["code"], err["message"]))
            await sync_to_async(_save_invocation)(
                owner=owner,
                conversation=conversation,
                run_uuid=run_uuid,
                step=state.step,
                rec=rec_b,
                call=call_b,
                decision="deny" if err["code"] in ("unknown_tool", "denied") else "auto",
                state="failed",
                ok=False,
                text="",
                error_code=err["code"],
            )
        if pending:
            for call, rec, approval_id in pending:
                await sync_to_async(_save_invocation)(
                    owner=owner,
                    conversation=conversation,
                    run_uuid=run_uuid,
                    step=state.step,
                    rec=rec,
                    call=call,
                    decision="pending",
                    state="awaiting_approval",
                )
                yield {
                    "type": "tool_approval_required",
                    "step": state.step,
                    "tool_use_id": call["id"],
                    "name": call["name"],
                    "approval_id": approval_id,
                }
            if error_blocks:
                state.messages.append({"role": "user", "content": error_blocks})
            yield {
                "type": "turn_paused",
                "pending": [
                    {
                        "tool_use_id": call["id"],
                        "stable_id": rec.stable_id,
                        "version": rec.version,
                        "name": call["name"],
                        "args": call["input"],
                        "approval_id": approval_id,
                    }
                    for call, rec, approval_id in pending
                ]
                + [
                    {
                        "tool_use_id": call["id"],
                        "stable_id": rec.stable_id,
                        "version": rec.version,
                        "name": call["name"],
                        "args": call["input"],
                        "approval_id": None,
                    }
                    for call, rec in deferred
                ]
                + [
                    {
                        "tool_use_id": call["id"],
                        "stable_id": (delegate["record"].stable_id if delegate else ""),
                        "version": (delegate["record"].version if delegate else ""),
                        "name": call["name"],
                        "args": call["input"],
                        "approval_id": None,
                    }
                    for call in delegated
                ],
                "loop": state.to_snapshot(),
            }
            return
        # Fase 2: sem pendência — autos sequenciais; delegações (M4) com no
        # máx 2 simultâneas (semáforo próprio, sem tocar o limite de autos).
        group_blocks: list[dict] = list(error_blocks)
        for call, rec in deferred:
            if state.invocations >= limits.MAX_INVOCATIONS:
                break
            events, block = await execute_one(
                call=call,
                rec=rec,
                approval=None,
                ctx=ctx,
                catalog=catalog,
                run_uuid=run_uuid,
                owner=owner,
                conversation=conversation,
                step=state.step,
                vision=vision,
            )
            for item in events:
                yield item
            state.invocations += 1
            group_blocks.append(block)
        if delegated and delegate is not None:
            from chat.services.agents import budget as _team_budget

            sem = asyncio.Semaphore(max(1, _team_budget.MAX_PARALLEL))
            slots: list = [None] * len(delegated)
            handle = delegate["handle"]

            async def _delegated_one(index: int, call: dict, _sem=sem, _slots=slots, _h=handle):
                async with _sem:
                    events, block = await _h(call)
                    _slots[index] = (events, block)

            await asyncio.gather(*[_delegated_one(i, call) for i, call in enumerate(delegated)])
            for events, block in slots:
                for item in events or []:
                    yield item
                state.invocations += 1
                group_blocks.append(block)
        state.messages.append({"role": "user", "content": group_blocks})
        state.step += 1
