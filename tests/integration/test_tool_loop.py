"""M1: loop explícito + ferramentas locais de leitura/cálculo (T1, T3).

Modelo simulado com fila de respostas; nenhum processo MCP é iniciado.
"""

from chat.services.tools import executor as ex
from chat.services.tools import local_tools, loop
from chat.services.tools.context import ExecutionContext


class ScriptedMessages:
    """Fila de respostas do modelo; registra chamadas (sem rede)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.script.pop(0)


class ScriptedClient:
    def __init__(self, script):
        self.messages = ScriptedMessages(script)


def _text_msg(text, stop="end_turn"):
    from types import SimpleNamespace

    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop,
        usage=SimpleNamespace(input_tokens=3, output_tokens=5),
        model="fake",
        id="msg_1",
    )


def _tool_msg(calls, stop="tool_use"):
    from types import SimpleNamespace

    return SimpleNamespace(
        content=[
            SimpleNamespace(type="tool_use", id=cid, name=name, input=args)
            for cid, name, args in calls
        ],
        stop_reason=stop,
        usage=SimpleNamespace(input_tokens=3, output_tokens=5),
        model="fake",
        id="msg_2",
    )


def _ctx():
    return ExecutionContext(user_id=7, conversation_id=9)


def _catalog(*names):
    recs = [r for r in local_tools.all_records() if r.stable_id.split(":")[1] in names]
    assert len(recs) == len(names)
    return recs


async def test_sem_ferramentas_sem_payload_sem_mcp():
    client = ScriptedClient([_text_msg("oi")])
    out = await loop.run_turn(
        client,
        model="m",
        system="s",
        messages=[{"role": "user", "content": "oi"}],
        max_tokens=10,
        catalog=[],
    )
    assert out.final_text == "oi"
    assert "tools" not in client.messages.calls[0]  # sem ferramentas no payload
    assert out.model_calls == 1 and out.invocations == 0


async def test_ciclo_calculo_com_ids_corretos():
    client = ScriptedClient(
        [
            _tool_msg([("tu_1", "local__calculate", {"op": "add", "a": 2, "b": 3})]),
            _text_msg("5"),
        ]
    )
    out = await loop.run_turn(
        client,
        model="m",
        system="s",
        messages=[{"role": "user", "content": "2+3?"}],
        max_tokens=10,
        catalog=_catalog("calculate"),
    )
    assert out.final_text == "5"
    assert out.invocations == 1
    cont = client.messages.calls[1]
    user_msg = cont["messages"][-1]
    assert user_msg["role"] == "user"  # protocolar, nunca role="tool"
    (res,) = user_msg["content"]
    assert res["tool_use_id"] == "tu_1" and "5" in str(res["content"])
    assistant_msg = cont["messages"][-2]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["content"][0]["id"] == "tu_1"  # tool_use preservado


async def test_multiplas_chamadas_todas_com_resultado():
    client = ScriptedClient(
        [
            _tool_msg(
                [
                    ("tu_1", "local__calculate", {"op": "mul", "a": 3, "b": 4}),
                    ("tu_2", "local__current_time", {"timezone": "America/Sao_Paulo"}),
                ]
            ),
            _text_msg("12 e agora"),
        ]
    )
    out = await loop.run_turn(
        client,
        model="m",
        system="s",
        messages=[{"role": "user", "content": "q"}],
        max_tokens=10,
        catalog=_catalog("calculate", "current_time"),
    )
    assert out.invocations == 2
    results = client.messages.calls[1]["messages"][-1]["content"]
    assert {r["tool_use_id"] for r in results} == {"tu_1", "tu_2"}


async def test_chamada_invalida_e_desconhecida_nao_executam():
    client = ScriptedClient(
        [
            _tool_msg(
                [
                    ("tu_1", "local__calculate", {"op": "div", "a": 1, "b": 0}),
                    ("tu_2", "local__nope", {"x": 1}),
                ]
            ),
            _text_msg("falhou, sem executar"),
        ]
    )
    out = await loop.run_turn(
        client,
        model="m",
        system="s",
        messages=[{"role": "user", "content": "q"}],
        max_tokens=10,
        catalog=_catalog("calculate"),
    )
    results = {r["tool_use_id"]: r for r in client.messages.calls[1]["messages"][-1]["content"]}
    assert results["tu_1"]["is_error"] is True  # divisão por zero: erro estruturado
    assert results["tu_2"]["is_error"] is True  # desconhecida: erro, sem execução
    assert out.final_text == "falhou, sem executar"


async def test_calculate_sem_eval_e_limites():
    ctx = _ctx()
    assert (await ex.execute("local__calculate", {"op": "add", "a": 1, "b": 2}, ctx))["ok"]
    bad = await ex.execute("local__calculate", {"op": "add", "a": float("inf"), "b": 1}, ctx)
    assert bad["ok"] is False  # não finito rejeitado, sem interpretar código
    big = await ex.execute("local__calculate", {"op": "mul", "a": 1e13, "b": 2}, ctx)
    assert big["ok"] is False


async def test_current_time_fuso_validado():
    ctx = _ctx()
    ok = await ex.execute("local__current_time", {"timezone": "America/Sao_Paulo"}, ctx)
    assert ok["ok"] and "Sao_Paulo" in ok["text"]
    bad = await ex.execute("local__current_time", {"timezone": "../etc"}, ctx)
    assert bad["ok"] is False
