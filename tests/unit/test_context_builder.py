"""Critérios 3, 4, 5, 6 (RF-08/RF-09)."""

import pytest

from chat.services.context_builder import (
    BudgetExceeded,
    CountFailed,
    build_context,
    build_turns,
)


def hist(*msgs):
    return [{"seq": i + 1, "role": r, "text": t, "state": s} for i, (r, t, s) in enumerate(msgs)]


async def counter_1(model, system, messages):
    return len(messages)  # 1 token por mensagem: orçamento vira "nº de mensagens"


@pytest.mark.asyncio
async def test_three_turns_order_and_current_once():
    """Critério 3: três turnos reconstruídos em ordem; atual exatamente 1x."""
    h = hist(
        ("user", "q1", "ok"),
        ("assistant", "a1", "ok"),
        ("user", "q2", "ok"),
        ("assistant", "a2", "ok"),
        ("user", "q3", "ok"),
        ("assistant", "a3", "ok"),
    )
    ctx = await build_context(
        history=h,
        current_text="AGORA",
        system="SYS",
        model="m",
        max_tokens=10,
        input_budget=10_000,
        count_tokens=counter_1,
    )
    roles = [m["role"] for m in ctx.messages]
    assert roles == ["user", "assistant"] * 3 + ["user"]
    assert [m["content"] for m in ctx.messages].count("AGORA") == 1
    assert ctx.system == "SYS"  # system separado, não dentro de messages
    assert "system" not in [m["role"] for m in ctx.messages]


@pytest.mark.asyncio
async def test_incomplete_turns_excluded_but_kept_in_db():
    """Critério 4: turnos falhos/cancelados fora do contexto automático."""
    h = hist(
        ("user", "q1", "ok"),
        ("assistant", "a1", "ok"),
        ("user", "q2", "ok"),
        ("assistant", "parcial", "cancelled"),
        ("user", "q3", "ok"),
        ("assistant", "", "failed"),
    )
    ctx = await build_context(
        history=h,
        current_text="AGORA",
        system="",
        model="m",
        max_tokens=10,
        input_budget=10_000,
        count_tokens=counter_1,
    )
    texts = [m["content"] for m in ctx.messages]
    assert "a1" in texts and "q1" in texts
    assert "parcial" not in texts and "q2" not in texts and "q3" not in texts
    assert texts[-1] == "AGORA"


@pytest.mark.asyncio
async def test_budget_removes_only_whole_turns():
    """Critério 5: orçamento remove turnos completos antigos; banco intacto."""
    h = hist(
        ("user", "q1", "ok"),
        ("assistant", "a1", "ok"),
        ("user", "q2", "ok"),
        ("assistant", "a2", "ok"),
        ("user", "q3", "ok"),
        ("assistant", "a3", "ok"),
    )
    # 7 mensagens contam 7; orçamento 3 → só cabem turno 3 + atual; caem turnos 1 e 2.
    ctx = await build_context(
        history=h,
        current_text="AGORA",
        system="",
        model="m",
        max_tokens=10,
        input_budget=3,
        count_tokens=counter_1,
    )
    texts = [m["content"] for m in ctx.messages]
    assert texts == ["q3", "a3", "AGORA"]
    assert ctx.omitted_turns == 2
    assert len(h) == 6  # redução nunca apaga o banco


@pytest.mark.asyncio
async def test_strict_mode_blocks_instead_of_trimming():
    h = hist(("user", "q1", "ok"), ("assistant", "a1", "ok"))
    with pytest.raises(BudgetExceeded):
        await build_context(
            history=h,
            current_text="AGORA",
            system="",
            model="m",
            max_tokens=10,
            input_budget=2,
            strict=True,
            count_tokens=counter_1,
        )


@pytest.mark.asyncio
async def test_minimum_over_budget_refuses_without_calling():
    """Critério 6: system+atual acima do limite → sem geração."""

    async def big(model, system, messages):
        return 999_999

    with pytest.raises(BudgetExceeded):
        await build_context(
            history=[],
            current_text="oi",
            system="S" * 10,
            model="m",
            max_tokens=10,
            input_budget=10,
            count_tokens=big,
        )


@pytest.mark.asyncio
async def test_count_failure_is_recoverable_error():
    """Critério 6: falha de contagem interrompe com erro recuperável."""

    async def boom(model, system, messages):
        raise RuntimeError("rede caiu")

    with pytest.raises(CountFailed):
        await build_context(
            history=[],
            current_text="oi",
            system="",
            model="m",
            max_tokens=10,
            input_budget=10,
            count_tokens=boom,
        )


def test_build_turns_orphan_user_kept_out():
    turns, rest = build_turns(hist(("user", "solta", "ok")))
    assert turns == [] and len(rest) == 1
