"""M4 integrado: reencontro pós-restart, bloqueio sem visão, RAG+imagens,
tool_result com imagem, protocolo entre turnos e revogação."""

import io
import json

import pytest
from asgiref.sync import sync_to_async
from django.db import connections
from PIL import Image

from chat.services.generation import execute_run, reserve_run
from tests.fakes import (
    FakeClient,
    FakeFinalMessage,
    FakeMessagesNamespace,
    FakeStream,
    FakeStreamManager,
)

pytestmark = pytest.mark.django_db(transaction=True)
adb = sync_to_async


def _png(size=(96, 72), color=(20, 90, 160)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _client_ok(text="pronto"):
    mgr = FakeStreamManager(FakeStream(["ok"], FakeFinalMessage(text)))
    return FakeClient(FakeMessagesNamespace(stream_manager=mgr))


async def _item():
    from chat.services.images import validate_image_bytes

    return await adb(validate_image_bytes)(_png())


async def _run(user, conversation, content, key, images=None, client=None):
    run, _, _ = await adb(reserve_run)(
        conversation=conversation,
        content=content,
        idempotency_key=key,
        images=images or [],
    )
    client = client or _client_ok()
    events = [e async for e in execute_run(str(run.uuid), user=user, client=client)]
    return run, events, client


def _started(events):
    return next(e for e in events if e["type"] == "run_started")


async def test_reencontro_pos_restart_sem_duplicar(user, conversation):
    item = await _item()
    await _run(user, conversation, "o que há aqui?", "m4-r1", images=[item])
    # Restart: fecha todas as conexões; o anexo reencontrado vem do disco.
    await adb(connections.close_all)()
    from chat.models import Message

    msg = await adb(Message.objects.get)(conversation=conversation, role="user", seq=1)
    from chat.services.images import verified_image_entries

    entries = await adb(verified_image_entries)(msg.blocks)
    assert entries[0]["data"] == item["data"]
    assert entries[0]["sha256"] == item["sha256"]

    _, events, client = await _run(user, conversation, "e agora?", "m4-r2")
    assert events[-1]["type"] == "done"
    sent = client.messages.calls["stream"][0]["messages"]
    assert sent[-1] == {"role": "user", "content": "e agora?"}  # atual sem duplicar
    history_user = sent[-3]
    assert history_user["role"] == "user"
    kinds = [b["type"] for b in history_user["content"]]
    assert kinds == ["text", "image"]  # anexo reenviado do histórico
    assert history_user["content"][1]["source"]["data"] == item["data"]


async def test_modelo_sem_visao_bloqueia_com_oferta(user, conversation, monkeypatch):
    from chat.services import thinking as _thinking

    monkeypatch.setattr(_thinking, "vision_for", lambda **kw: "no")
    item = await _item()
    run, _, _ = await adb(reserve_run)(
        conversation=conversation,
        content="descreva",
        idempotency_key="m4-nov",
        images=[item],
    )
    client = _client_ok()
    events = [e async for e in execute_run(str(run.uuid), user=user, client=client)]
    err = next(e for e in events if e["type"] == "error")
    assert err["code"] == "vision_unsupported"
    assert "Troque o modelo" in err["message"]
    assert client.messages.calls["stream"] == []  # sem chamada paga
    await adb(run.refresh_from_db)()
    assert run.state == "failed"


async def test_rag_textual_junto_de_imagens_texto_unico(user, conversation, monkeypatch):
    from chat.services.rag import answer as ans
    from chat.services.rag.answer import RagPrep
    from chat.services.rag.retrieval import EvidenceHit

    hit = EvidenceHit(
        chunk_id=1,
        chunk_uuid="c1",
        base_uuid="b1",
        base_name="base",
        doc_name="doc",
        version_number=1,
        text="trecho documental",
        locator={},
        rrf_score=1.0,
        rank_lexical=1,
        rank_vector=1,
    )
    monkeypatch.setattr(
        ans,
        "prepare",
        lambda uid, conv, q: RagPrep(
            status="ready", evidences=[hit], effective_query=q, run_id=None, sent_chars=17
        ),
    )
    item = await _item()
    _, events, client = await _run(user, conversation, "pergunta?", "m4-rag", images=[item])
    assert events[-1]["type"] == "done"
    current = client.messages.calls["stream"][0]["messages"][-1]
    kinds = [b["type"] for b in current["content"]]
    assert kinds == ["text", "image", "search_result"]
    assert current["content"][0] == {"type": "text", "text": "pergunta?"}


async def test_tool_result_com_imagem_autorizada(user, conversation, monkeypatch):
    from chat.services.tools import chat_loop
    from chat.services.tools import executor as ex
    from chat.services.tools.context import ExecutionContext
    from chat.services.tools.registry import ToolRecord

    item = await _item()

    async def _handler(args, ctx):
        return {
            "ok": True,
            "text": "veja",
            "images": [{"media_type": item["media_type"], "data": item["data"]}],
        }

    monkeypatch.setitem(ex.HANDLERS, "local__pictool", _handler)
    rec = ToolRecord(
        stable_id="local:pictool",
        origin="local",
        original_name="pictool",
        description="d",
        input_schema={"type": "object"},
        version="t1",
        approval="auto",
        supports_images=True,
    )
    ctx = ExecutionContext(user_id=user.pk, conversation_id=conversation.pk)
    call = {"id": "tu_1", "name": "local__pictool", "input": {}}
    common = dict(
        call=call,
        rec=rec,
        approval=None,
        ctx=ctx,
        catalog=[rec],
        run_uuid="r1",
        owner=user,
        conversation=conversation,
        step=0,
    )
    events, block = await chat_loop.execute_one(**common, vision="yes")
    assert events[-1] == {"type": "tool_finished", "step": 0, "tool_use_id": "tu_1", "ok": True}
    kinds = [b["type"] for b in block["content"]]
    assert kinds == ["text", "image"]
    assert block["content"][1]["source"]["data"] == item["data"]  # Base64 integral


async def test_tool_result_imagem_descartada_sem_visao(user, conversation, monkeypatch):
    from chat.services.tools import chat_loop
    from chat.services.tools import executor as ex
    from chat.services.tools.context import ExecutionContext
    from chat.services.tools.registry import ToolRecord

    item = await _item()

    async def _handler(args, ctx):
        return {
            "ok": True,
            "text": "veja",
            "images": [{"media_type": item["media_type"], "data": item["data"]}],
        }

    monkeypatch.setitem(ex.HANDLERS, "local__pictool2", _handler)
    rec = ToolRecord(
        stable_id="local:pictool2",
        origin="local",
        original_name="pictool2",
        description="d",
        input_schema={"type": "object"},
        version="t1",
        approval="auto",
        supports_images=True,
    )
    ctx = ExecutionContext(user_id=user.pk, conversation_id=conversation.pk)
    _, block = await chat_loop.execute_one(
        call={"id": "tu_9", "name": "local__pictool2", "input": {}},
        rec=rec,
        approval=None,
        ctx=ctx,
        catalog=[rec],
        run_uuid="r9",
        owner=user,
        conversation=conversation,
        step=0,
        vision="no",
    )
    assert block["type"] == "tool_result"
    assert "vision_unsupported" in block["content"]
    assert item["data"] not in json.dumps(block)  # sem bytes no estado


async def test_protocolo_prefixo_mudanca_gera_aviso(user, conversation):
    _, e1, _ = await _run(user, conversation, "a", "m4-p1")
    p1 = _started(e1)["protocol"]
    assert (p1["generation"], p1["prefix_changed"]) == (1, False)

    _, e2, _ = await _run(user, conversation, "b", "m4-p2")
    p2 = _started(e2)["protocol"]
    assert (p2["generation"], p2["prefix_changed"]) == (1, False)
    assert p2["warning"] is None

    conversation.system_prompt = "responda em tópicos"
    await adb(conversation.save)()
    _, e3, _ = await _run(user, conversation, "c", "m4-p3")
    p3 = _started(e3)["protocol"]
    assert (p3["generation"], p3["prefix_changed"]) == (2, True)
    assert "mudou" in (p3["warning"] or "")


async def test_anexo_adulterado_revoga_sem_chamada(user, conversation):
    item = await _item()
    run, _, _ = await adb(reserve_run)(
        conversation=conversation,
        content="descreva",
        idempotency_key="m4-tam",
        images=[item],
    )
    from chat.models import Message

    msg = await adb(Message.objects.get)(pk=run.user_message.pk)
    blocks = [dict(b) for b in msg.blocks]
    data = blocks[1]["source"]["data"]
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    swapped = alphabet[(alphabet.index(data[100]) + 1) % len(alphabet)]
    blocks[1]["source"]["data"] = data[:100] + swapped + data[101:]
    blocks[1]["source"] = dict(blocks[1]["source"])
    await adb(Message.objects.filter(pk=msg.pk).update)(blocks=blocks)

    client = _client_ok()
    events = [e async for e in execute_run(str(run.uuid), user=user, client=client)]
    err = next(e for e in events if e["type"] == "error")
    assert err["code"] == "attachment_revoked"
    assert client.messages.calls["stream"] == []
    assert item["data"] not in json.dumps(events)


def test_resume_ferramenta_revogada(user, conversation):
    from chat.services.tools.chat_loop import check_resume_entry
    from chat.services.tools.registry import ToolRecord

    rec = ToolRecord(
        stable_id="local:calculate",
        origin="local",
        original_name="calculate",
        description="d",
        input_schema={"type": "object"},
        version="t2",
        approval="auto",
    )
    entry = {
        "tool_use_id": "tu_1",
        "stable_id": "local:calculate",
        "version": "t1",
        "name": "local__calculate",
        "args": {},
        "approval_id": None,
    }
    _, _, err = check_resume_entry(entry, [rec], user, "run-x")
    assert err is not None
    assert err[0] == "revoked"
