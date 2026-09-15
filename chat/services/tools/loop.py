"""Loop explícito de tool calling (T3, ADR-T1/T2).

Uma solicitação = até MAX_MODEL_STEPS chamadas `messages.create` (não-stream)
e até MAX_INVOCATIONS execuções sequenciais. `tool_use` preservado no bloco
assistant; `tool_result` em mensagem `user` com IDs originais. Sem runner
automático: cada etapa passa por validação antes de qualquer execução.
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
class TurnResult:
    final_text: str
    messages: list[dict]
    model_calls: int
    invocations: int
    usage: dict = field(default_factory=dict)
    stopped: str = "end_turn"  # end_turn | limit | budget | failed


def _blocks_of(message) -> list[dict]:
    blocks = []
    for block in getattr(message, "content", None) or []:
        btype = getattr(block, "type", "")
        if btype == "text":
            blocks.append({"type": "text", "text": getattr(block, "text", "")})
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


async def run_turn(
    client,
    *,
    model: str,
    system: str,
    messages: list[dict],
    max_tokens: int,
    catalog: list[ToolRecord],
    ctx: ExecutionContext | None = None,
    extra_body: dict | None = None,
) -> TurnResult:
    from chat.services.tools.local_tools import all_records

    working = [dict(m) for m in messages]
    payload = build_payload(catalog)
    calls = 0
    invoked = 0
    usage = {"input_tokens": None, "output_tokens": None}
    started = time.monotonic()
    active_ctx = ctx or ExecutionContext(user_id=0, conversation_id=0)
    known = {r.anthropic_name: r for r in (catalog or all_records())}

    while True:
        if calls >= limits.MAX_MODEL_STEPS:
            return TurnResult("", working, calls, invoked, usage, stopped="limit")
        if time.monotonic() - started > limits.ACTIVE_BUDGET_S:
            return TurnResult("", working, calls, invoked, usage, stopped="budget")
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [dict(m) for m in working],  # snapshot da etapa
            "system": [{"type": "text", "text": system}] if system else None,
            "max_tokens": max_tokens,
        }
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        kwargs.update(payload)
        if extra_body:
            kwargs["extra_body"] = extra_body
        message = await client.messages.create(**kwargs)
        calls += 1
        for key in ("input_tokens", "output_tokens"):
            value = getattr(getattr(message, "usage", None), key, None)
            if value is not None:
                usage[key] = value
        blocks = _blocks_of(message)
        working.append({"role": "assistant", "content": blocks})
        tool_calls = [b for b in blocks if b["type"] == "tool_use"]
        if not tool_calls or getattr(message, "stop_reason", "") != "tool_use":
            text = "".join(b.get("text", "") for b in blocks if b["type"] == "text")
            return TurnResult(text, working, calls, invoked, usage)
        results = []
        for call in tool_calls:
            if invoked >= limits.MAX_INVOCATIONS:
                break
            rec = known.get(call["name"])
            if rec is None:
                outcome: dict[str, Any] = _executor.unknown_tool(call["name"])
            else:
                outcome = await _executor.execute(call["name"], call["input"], active_ctx)
            invoked += 1
            if outcome.get("ok"):
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call["id"],
                        "content": outcome["text"],
                    }
                )
            else:
                err = outcome["error"]
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call["id"],
                        "content": f"Erro {err['code']}: {err['message']}",
                        "is_error": True,
                    }
                )
        working.append({"role": "user", "content": results})
