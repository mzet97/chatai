"""Reconstrução determinística do payload (RF-08) + orçamento (RF-09).

Distinção: histórico persistido (tudo) ≠ contexto enviado (turnos válidos) ≠
instruções (system + config). Redução remove turnos COMPLETOS mais antigos.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BuiltContext:
    system: str
    messages: list[dict]  # [{"role": "user"|"assistant", "content": ...}]
    included_seqs: list[int]
    omitted_turns: int
    estimated_tokens: int | None  # contagem exata ou estimativa didática
    count_is_estimate: bool
    model: str
    max_tokens: int


def _estimate_tokens(system: str, messages: list[dict]) -> int:
    """Estimativa didática (NÃO é contagem exata): ~4 chars/token + margem."""
    chars = len(system or "")
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, str):
            chars += len(c)
        else:
            for b in c:
                if isinstance(b, dict) and b.get("type") == "text":
                    chars += len(b.get("text", ""))
    return chars // 4 + len(messages) * 8 + 16


def build_turns(history: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separa turnos válidos (user+assistant ok) de mensagens avulsas.

    history: [{"seq": int, "role": str, "text": str, "state": str}] em ordem de seq.
    Retorna (turnos_completos, resto). Turno completo = user seguido de assistant ok.
    Só a resposta aceita (state == "ok") entra no contexto automático.
    """
    turns, rest, pending_user = [], [], None
    for m in history:
        if m["role"] == "user":
            if pending_user is not None:
                rest.append(pending_user)  # user anterior sem resposta válida
            pending_user = m
        elif m["role"] == "assistant":
            if pending_user is not None and m["state"] == "ok":
                turns.append((pending_user, m))
                pending_user = None
            else:
                if pending_user is not None:
                    rest.append(pending_user)
                    pending_user = None
                rest.append(m)
    if pending_user is not None:
        rest.append(pending_user)
    return turns, rest


async def build_context(
    *,
    history: list[dict],
    current_text: str,
    system: str,
    model: str,
    max_tokens: int,
    input_budget: int,
    strict: bool = False,
    count_tokens=None,
) -> BuiltContext:
    """Monta payload com orçamento. count_tokens: async (model, system, messages) -> int.

    - Sem count_tokens: usa estimativa (marcada como tal).
    - Falha na contagem no fluxo padrão: levanta CountFailed (erro recuperável).
    - strict: em vez de reduzir, levanta BudgetExceeded.
    - system+atual acima do limite: levanta BudgetExceeded sem chamar a API.
    """
    turns, _ = build_turns(history)
    current_msg = {"role": "user", "content": current_text}

    async def count(sys: str, msgs: list[dict]) -> int:
        if count_tokens is not None:
            return await count_tokens(model=model, system=sys, messages=msgs)
        return _estimate_tokens(sys, msgs)

    is_estimate = count_tokens is None
    try:
        minimum = await count(system, [current_msg])
    except Exception as exc:
        raise CountFailed(str(exc)) from exc
    if minimum > input_budget:
        raise BudgetExceeded(
            f"system prompt + mensagem atual ({minimum} tokens) excedem o orçamento "
            f"({input_budget}). Reduza o conteúdo ou escolha maior capacidade."
        )
    # Remove turnos completos mais antigos até caber; reconta o resultado.
    kept = list(turns)
    omitted = 0
    try:
        while kept:
            candidate = []
            for u, a in kept:
                candidate += [
                    {"role": "user", "content": u["text"]},
                    {"role": "assistant", "content": a["text"]},
                ]
            total = await count(system, candidate + [current_msg])
            if total <= input_budget:
                break
            kept.pop(0)
            omitted += 1
        else:
            total = minimum
        final_msgs = []
        for u, a in kept:
            final_msgs += [
                {"role": "user", "content": u["text"]},
                {"role": "assistant", "content": a["text"]},
            ]
        final_msgs.append(current_msg)
        if kept:
            try:
                total = await count(system, final_msgs)
            except Exception as exc:
                raise CountFailed(str(exc)) from exc
        else:
            total = minimum
    except (BudgetExceeded, CountFailed):
        raise
    if omitted and strict:
        raise BudgetExceeded(
            f"{omitted} turno(s) ficariam de fora e o modo estrito bloqueia o envio."
        )
    included = [s for t in kept for s in (t[0]["seq"], t[1]["seq"])]
    return BuiltContext(
        system=system,
        messages=final_msgs,
        included_seqs=included,
        omitted_turns=omitted,
        estimated_tokens=total,
        count_is_estimate=is_estimate,
        model=model,
        max_tokens=max_tokens,
    )


class BudgetExceeded(Exception):
    pass


class CountFailed(Exception):
    pass
