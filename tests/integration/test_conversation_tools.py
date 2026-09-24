"""M5: seleção por conversa, caminho com ferramentas e eventos SSE nos 2 modos."""

import json

import pytest
from asgiref.sync import sync_to_async

from chat.services.generation import execute_run, reserve_run
from tests.fakes import (
    FakeClient,
    FakeFinalMessage,
    FakeMessagesNamespace,
    FakeStream,
    FakeStreamManager,
    FakeToolMessage,
    FakeToolUseBlock,
)

pytestmark = pytest.mark.django_db(transaction=True)
adb = sync_to_async


def _stream_client(*, text="ok"):
    mgr = FakeStreamManager(FakeStream([text], FakeFinalMessage(text)))
    return FakeClient(FakeMessagesNamespace(stream_manager=mgr))


async def _collect(agen):
    return [ev async for ev in agen]


def _prefs_url(conversation):
    return f"/api/conversations/{conversation.uuid}/tools"


def test_prefs_default_vazio(logged_client, conversation):
    resp = logged_client.get(_prefs_url(conversation))
    assert resp.status_code == 200
    # M5 registra search_knowledge_base + read_knowledge_excerpt.
    assert resp.json() == {"enabled": [], "available": 6}


def test_put_valida_stable_ids(logged_client, conversation):
    bad = logged_client.put(
        _prefs_url(conversation),
        data=json.dumps({"enabled": ["nope:x"]}),
        content_type="application/json",
    )
    assert bad.status_code == 400
    ok = logged_client.put(
        _prefs_url(conversation),
        data=json.dumps({"enabled": ["local:calculate", "local:create_study_note"]}),
        content_type="application/json",
    )
    assert ok.status_code == 200
    assert sorted(ok.json()["enabled"]) == ["local:calculate", "local:create_study_note"]


async def test_sem_ferramentas_caminho_textual_sem_create(user, conversation):
    run, _, _ = await adb(reserve_run)(
        conversation=conversation, content="oi", idempotency_key="k-m5-1"
    )
    client = _stream_client(text="olá")
    events = await _collect(execute_run(str(run.uuid), user=user, client=client))
    assert client.messages.calls["create"] == []
    assert [e["type"] for e in events][-1] == "done"


async def test_ciclo_calculate_streaming_com_eventos(user, conversation):
    from chat.models_tools import ConversationToolPrefs

    await adb(ConversationToolPrefs.objects.create)(
        conversation=conversation, enabled=["local:calculate"]
    )
    run, _, _ = await adb(reserve_run)(
        conversation=conversation, content="quanto é 2+3?", idempotency_key="k-m5-2"
    )
    tool_msg = FakeToolMessage(
        [FakeToolUseBlock("tu_1", "local__calculate", {"op": "add", "a": 2, "b": 3})]
    )
    final = FakeFinalMessage("2 + 3 = 5")
    client = FakeClient(FakeMessagesNamespace(create_results=[tool_msg, final]))
    events = await _collect(execute_run(str(run.uuid), user=user, client=client))
    kinds = [e["type"] for e in events]
    assert "model_step_started" in kinds
    assert "tool_call_requested" in kinds
    assert "tool_started" in kinds
    assert "tool_finished" in kinds
    assert "text_delta" in kinds  # streaming emite parcial por etapa
    done = [e for e in events if e["type"] == "done"][-1]
    assert done["text"] == "2 + 3 = 5"
    assert kinds.count("done") == 1  # done só encerra o turno inteiro
    req = [e for e in events if e["type"] == "tool_call_requested"][0]
    assert req["tool_use_id"] == "tu_1" and req["step"] == 0
    fin = [e for e in events if e["type"] == "tool_finished"][0]
    assert fin["ok"] is True and fin["tool_use_id"] == "tu_1"


async def test_ciclo_modo_completo_retem_texto_mas_mostra_ferramentas(user, conversation):
    from chat.models import Conversation
    from chat.models_tools import ConversationToolPrefs

    await adb(Conversation.objects.filter(pk=conversation.pk).update)(response_mode="complete")
    await adb(ConversationToolPrefs.objects.create)(
        conversation=conversation, enabled=["local:calculate"]
    )
    run, _, _ = await adb(reserve_run)(
        conversation=conversation, content="2+3?", idempotency_key="k-m5-3"
    )
    tool_msg = FakeToolMessage(
        [FakeToolUseBlock("tu_9", "local__calculate", {"op": "add", "a": 2, "b": 3})]
    )
    client = FakeClient(
        FakeMessagesNamespace(create_results=[tool_msg, FakeFinalMessage("5")])
    )
    events = await _collect(execute_run(str(run.uuid), user=user, client=client))
    kinds = [e["type"] for e in events]
    assert "text_delta" not in kinds
    assert "tool_finished" in kinds  # atividade visível mesmo sem parcial
    done = [e for e in events if e["type"] == "done"][-1]
    assert done["text"] == "5"


async def test_escrita_pausa_decide_continua(user, conversation, logged_client):
    from chat.models_tools import ConversationToolPrefs, StudyNote, ToolApproval

    await adb(ConversationToolPrefs.objects.create)(
        conversation=conversation, enabled=["local:create_study_note"]
    )
    run, _, _ = await adb(reserve_run)(
        conversation=conversation, content="anote isso", idempotency_key="k-m5-4"
    )
    tool_msg = FakeToolMessage(
        [
            FakeToolUseBlock(
                "tu_w", "local__create_study_note", {"title": "T", "text": "corpo"}
            )
        ]
    )
    client = FakeClient(
        FakeMessagesNamespace(
            create_results=[tool_msg, FakeFinalMessage("anotado")]
        )
    )
    events = await _collect(execute_run(str(run.uuid), user=user, client=client))
    kinds = [e["type"] for e in events]
    assert "tool_approval_required" in kinds
    assert kinds[-1] == "run_paused"  # segmento HTTP encerra na pausa
    assert await adb(StudyNote.objects.count)() == 0
    req = [e for e in events if e["type"] == "tool_approval_required"][0]
    approval_id = req["approval_id"]

    # Texto no chat não aprova: recarregar a linha mostra pending.
    row = await adb(ToolApproval.objects.get)(public_id=approval_id)
    assert row.decision == "pending"

    # Endpoint decide (aprovar uma vez); cliente sync vai para thread.
    def _decide():
        return logged_client.post(
            f"/api/runs/{run.uuid}/approvals/{approval_id}/decide",
            data=json.dumps({"decision": "approve", "idempotency_key": "dec-m5"}),
            content_type="application/json",
        )

    dec = await adb(_decide)()
    assert dec.status_code == 200 and dec.json()["consumed"] is True
    # Continue retoma do estado persistido sem re-perguntar ao modelo.
    from chat.services.generation import resume_run

    resumed = FakeClient(
        FakeMessagesNamespace(create_results=[FakeFinalMessage("anotado")])
    )
    cont_events = await _collect(resume_run(str(run.uuid), user=user, client=resumed))
    kinds = [e["type"] for e in cont_events]
    assert "tool_finished" in kinds and kinds[-1] == "done"
    assert resumed.messages.calls["create"]  # uma nova chamada com o tool_result
    first_create = resumed.messages.calls["create"][0]
    assert first_create["messages"][-1]["role"] == "user"  # resultado, não pergunta
    assert await adb(StudyNote.objects.filter(title="T").count)() == 1
    await adb(run.refresh_from_db)()
    assert run.state == "done"


def test_endpoints_gating(logged_client, conversation):
    """Decide/continue: 400/404 sem tocar em execução alheia."""
    from chat.services.generation import reserve_run

    run, _, _ = reserve_run(
        conversation=conversation, content="oi", idempotency_key="k-m5-g"
    )
    cont = logged_client.post(f"/api/runs/{run.uuid}/continue")
    assert cont.status_code == 400  # não pausada
    cont = logged_client.post(
        "/api/runs/00000000-0000-0000-0000-000000000000/continue"
    )
    assert cont.status_code == 404
    bad = logged_client.post(
        f"/api/runs/{run.uuid}/approvals/00000000-0000-0000-0000-000000000000/decide",
        data=json.dumps({"decision": "approve"}),
        content_type="application/json",
    )
    assert bad.status_code == 404


async def test_tool_path_envia_historico_reconstruido(user, conversation):
    """C1: 1º messages.create do loop de tools carrega o payload do SQLite
    (nunca lista vazia) — invariante de payload reconstruído no ramo tools."""
    from chat.models import Message
    from chat.models_tools import ConversationToolPrefs

    await adb(Message.objects.create)(
        conversation=conversation, seq=1, role="user", text="guarda: abacaxi", state="ok"
    )
    await adb(Message.objects.create)(
        conversation=conversation, seq=2, role="assistant", text="guardado", state="ok"
    )
    await adb(ConversationToolPrefs.objects.create)(
        conversation=conversation, enabled=["local:calculate"]
    )
    run, _, _ = await adb(reserve_run)(
        conversation=conversation, content="quanto é 2+3?", idempotency_key="k-c1-base"
    )
    tool_msg = FakeToolMessage(
        [FakeToolUseBlock("tu_c1", "local__calculate", {"op": "add", "a": 2, "b": 3})]
    )
    client = FakeClient(FakeMessagesNamespace(create_results=[tool_msg, FakeFinalMessage("5")]))
    events = await _collect(execute_run(str(run.uuid), user=user, client=client))
    assert [e["type"] for e in events][-1] == "done"
    creates = client.messages.calls["create"]
    assert creates, "loop de tools não chamou o provedor"
    first = creates[0]
    msgs = first.get("messages") or []
    assert msgs, "tool path iniciou com messages=[] (C1)"
    roles = [m.get("role") for m in msgs]
    assert roles[0] == "user"
    flat = str(msgs)
    assert "abacaxi" in flat  # histórico reconstruído
    assert "2+3" in flat  # mensagem atual
    # system do caminho textual deve acompanhar o tool path
    assert first.get("system"), "system ausente no tool path"
