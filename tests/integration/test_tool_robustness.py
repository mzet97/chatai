"""Fase 2 do plano: falha de tool vira tool_result; catálogo vazio não expande."""

import pytest

from chat.services.tools import executor as ex
from chat.services.tools import local_tools, loop
from chat.services.tools.context import ExecutionContext
from chat.services.tools.local_tools import HANDLERS

pytestmark = pytest.mark.integration


def _ctx():
    return ExecutionContext(user_id=7, conversation_id=9)


def _calc_rec():
    recs = [r for r in local_tools.all_records() if r.anthropic_name == "local__calculate"]
    assert len(recs) == 1
    return recs


async def test_handler_que_levanta_vira_erro_classificado(monkeypatch):
    async def boom(args, ctx):
        raise RuntimeError("pane")

    monkeypatch.setitem(HANDLERS, "local__calculate", boom)
    out = await ex.execute("local__calculate", {"op": "add", "a": 1, "b": 2}, _ctx(), _calc_rec())
    assert out["ok"] is False
    assert out["error"]["code"]


async def test_mcp_sem_transporte_ou_falha_nao_levanta():
    out = await ex.execute("local__calculate", {"op": "add", "a": 1, "b": 2}, _ctx(), [])
    assert out["ok"] is False
    assert out["error"]["code"] == "unknown_tool"


async def test_catalogo_vazio_nao_executa_nada():
    from types import SimpleNamespace

    def tool_msg():
        return SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", id="tu_1", name="x__y", input={})],
            stop_reason="tool_use",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            model="fake",
            id="msg_2",
        )

    def text_msg():
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="fim")],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            model="fake",
            id="msg_3",
        )

    class C:
        def __init__(self):
            self.messages = self
            self.calls = []
            self.script = [tool_msg(), text_msg()]

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            return self.script.pop(0)

    client = C()
    out = await loop.run_turn(
        client,
        model="m",
        system="s",
        messages=[{"role": "user", "content": "oi"}],
        max_tokens=10,
        catalog=[],
    )
    assert out.final_text == "fim"
    assert "tools" not in client.calls[0]
    last_user = client.calls[1]["messages"][-1]
    (res,) = last_user["content"]
    assert res["tool_use_id"] == "tu_1"
    assert "unknown_tool" in str(res["content"])
