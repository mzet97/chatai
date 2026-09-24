"""M3: servidor MCP demo + cliente stdio real (T6, sem Anthropic)."""

import asyncio
import os
import subprocess
import sys

import pytest
from asgiref.sync import sync_to_async as _sync_to_async

from chat.services.tools import mcp_client
from chat.services.tools.registry import ToolRecord, anthropic_name_for

_adb = _sync_to_async

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVER = os.path.join(ROOT, "mcp_servers", "study_lessons", "server.py")
PYTHON = sys.executable

pytestmark = pytest.mark.django_db(transaction=True)


def _stdio():
    return mcp_client.StdioConfig(command=PYTHON, args=[SERVER])


async def test_descoberta_lista_licoes():
    info = await mcp_client.list_tools_stdio(_stdio())
    assert info.protocol_version  # resposta protocolar válida, não só porta aberta
    names = {t["name"] for t in info.tools}
    assert {"list_lessons", "read_lesson"} <= names
    read = next(t for t in info.tools if t["name"] == "read_lesson")
    assert read["inputSchema"]["type"] == "object"


async def test_chamada_valida_e_id_invalido():
    ok = await mcp_client.call_tool_stdio(_stdio(), "read_lesson", {"lesson_id": "tdd"})
    assert ok["ok"] is True and "TDD" in ok["text"]
    bad = await mcp_client.call_tool_stdio(_stdio(), "read_lesson", {"lesson_id": "../../etc"})
    assert bad["ok"] is False  # conjunto fechado, sem caminho arbitrário
    listed = await mcp_client.call_tool_stdio(_stdio(), "list_lessons", {})
    assert "tdd" in listed["text"]


async def test_nomes_mapeados_sem_colisao():
    info = await mcp_client.list_tools_stdio(_stdio())
    recs = mcp_client.records_for("conn-1", "study-lessons", info, version="lessons-v1")
    assert all(isinstance(r, ToolRecord) for r in recs)
    names = [r.anthropic_name for r in recs]
    assert len(set(names)) == len(names)
    assert anthropic_name_for("mcp", "study-lessons", "read_lesson") in names


async def test_stdio_nao_herda_segredos():
    env = mcp_client.build_env({})
    for banned in ("ANTHROPIC_API_KEY", "DJANGO_SECRET_KEY", "CHAT_DB_PATH"):
        assert banned not in env
        os.environ[f"_{banned}_PROBE"] = "x"
    assert all(not k.endswith("_PROBE") for k in env)


async def test_sem_processo_orfao_apos_fechar():
    def _count():
        out = subprocess.run(
            ["ps", "-eo", "args"], capture_output=True, text=True, timeout=10
        ).stdout
        return sum(1 for line in out.splitlines() if "study_lessons/server.py" in line)

    before = _count()
    await mcp_client.call_tool_stdio(_stdio(), "list_lessons", {})
    await asyncio.sleep(0.5)
    assert _count() <= before  # processo do teste encerrado, sem órfão


async def test_ciclo_executor_rota_mcp():
    from chat.services.tools import executor
    from chat.services.tools.context import ExecutionContext

    async def fake_mcp(name, args):
        assert name == "mcp__study-lessons__read_lesson"
        return {"ok": True, "text": "lição fictícia"}

    recs = [
        ToolRecord(
            stable_id="mcp:conn-1:read_lesson",
            origin="mcp",
            scope="study-lessons",
            original_name="read_lesson",
            description="Lê lição.",
            input_schema={"type": "object", "properties": {}},
            version="lessons-v1",
            approval="auto",
        )
    ]
    out = await executor.execute(
        "mcp__study-lessons__read_lesson",
        {},
        ExecutionContext(user_id=1, conversation_id=1),
        records=recs,
        mcp_call=fake_mcp,
    )
    assert out["ok"] and "fictícia" in out["text"]


async def test_execute_authorized_pluga_transporte_mcp(user, conversation):
    """C2: execute_authorized resolve o transporte da conexão (não `unavailable`)."""
    import uuid as uuid_mod

    from chat.models_tools import MCPConnection
    from chat.services.tools import executor
    from chat.services.tools.context import ExecutionContext

    conn = await _adb(MCPConnection.objects.create)(
        owner=user,
        alias=f"study-{uuid_mod.uuid4().hex[:8]}",
        transport="stdio",
        config={"command": PYTHON, "args": [SERVER]},
        state="active",
        revision=1,
    )
    recs = [
        ToolRecord(
            stable_id=f"mcp:{conn.uuid}:read_lesson",
            origin="mcp",
            scope=conn.alias,
            original_name="read_lesson",
            description="Lê lição.",
            input_schema={
                "type": "object",
                "properties": {"lesson_id": {"type": "string"}},
                "required": ["lesson_id"],
            },
            version="r1",
            approval="auto",
        )
    ]
    out = await executor.execute_authorized(
        recs[0].anthropic_name,
        {"lesson_id": "tdd"},
        ExecutionContext(user_id=user.pk, conversation_id=conversation.pk),
        tool_use_id="tu_mcp_c2",
        approval=None,
        records=recs,
        run_uuid="run-c2",
    )
    assert out.get("ok") is True, out
    assert "TDD" in out.get("text", "")


async def test_execute_authorized_mcp_conexao_inativa(user, conversation):
    """C2: conexão disabled/negada nunca executa efeito."""
    import uuid as uuid_mod

    from chat.models_tools import MCPConnection
    from chat.services.tools import executor
    from chat.services.tools.context import ExecutionContext

    conn = await _adb(MCPConnection.objects.create)(
        owner=user,
        alias=f"off-{uuid_mod.uuid4().hex[:8]}",
        transport="stdio",
        config={"command": PYTHON, "args": [SERVER]},
        state="disabled",
        revision=1,
    )
    recs = [
        ToolRecord(
            stable_id=f"mcp:{conn.uuid}:list_lessons",
            origin="mcp",
            scope=conn.alias,
            original_name="list_lessons",
            description="Lista.",
            input_schema={"type": "object", "properties": {}},
            version="r1",
            approval="auto",
        )
    ]
    out = await executor.execute_authorized(
        recs[0].anthropic_name,
        {},
        ExecutionContext(user_id=user.pk, conversation_id=conversation.pk),
        tool_use_id="tu_mcp_off",
        approval=None,
        records=recs,
        run_uuid="run-c2-off",
    )
    assert out.get("ok") is False
    assert out["error"]["code"] == "unavailable"
