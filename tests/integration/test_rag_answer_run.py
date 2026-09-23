"""M4: geração com evidências e abstenção, SDK simulado (RAG-06/08)."""

import pytest
from asgiref.sync import sync_to_async

from chat.models import Conversation
from chat.models_rag import (
    ConversationKnowledge,
    Document,
    DocumentVersion,
    IngestionJob,
    KnowledgeBase,
)
from chat.services.generation import execute_run, reserve_run
from chat.services.rag import embed as _embed
from chat.services.rag.worker import process_job
from tests.fakes import FakeClient, FakeMessagesNamespace, FakeStream, FakeStreamManager

pytestmark = pytest.mark.django_db(transaction=True)
adb = sync_to_async

try:
    _PREPARED = _embed.is_prepared()
except Exception:
    _PREPARED = False

needs_model = pytest.mark.skipif(not _PREPARED, reason="modelo não preparado")


def _client_ok(text="Resposta com [1]."):
    from tests.fakes import FakeFinalMessage

    final = FakeFinalMessage(text)
    mgr = FakeStreamManager(FakeStream([text[:4], text[4:]], final))
    ns = FakeMessagesNamespace(stream_manager=mgr)
    return FakeClient(ns), ns


@pytest.fixture
def setup(db, tmp_path):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user("a4i", password="pw123456")
    conv = Conversation.objects.create(owner=user, title="t")
    kb = KnowledgeBase.objects.create(owner=user, name="Base")
    doc = Document.objects.create(base=kb, owner=user, name="doc.txt")
    content = "O prazo de entrega é 30 dias corridos.".encode()
    ver = DocumentVersion.objects.create(
        document=doc, number=1, sha256="aa" * 32, filename="doc.txt",
        size_bytes=len(content), rel_path=f"{doc.uuid}/doc.txt",
    )
    job = IngestionJob.objects.create(owner=user, document=doc, version=ver)
    p = tmp_path / "doc.txt"
    p.write_bytes(content)
    return user, conv, kb, job, ver, p


async def _reserve(user, conv, text):
    return await adb(reserve_run)(
        conversation=conv, content=text, idempotency_key=f"k-{text[:8]}"
    )


@needs_model
async def test_pronta_envia_search_result_e_finaliza(setup):
    user, conv, kb, job, ver, p = setup
    assert await adb(process_job)(job.uuid, "w", file_map={str(ver.uuid): p}) == "ready"
    await adb(ConversationKnowledge.objects.create)(
        conversation=conv, bases=[str(kb.uuid)], mode="always"
    )
    run, _, _ = await _reserve(user, conv, "Qual o prazo?")
    client, ns = _client_ok()
    events = [e async for e in execute_run(str(run.uuid), user=user, client=client)]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "run_started"
    assert events[0]["rag"]["status"] == "ready"
    assert ns.calls["stream"], "geração paga deve ocorrer com evidências"
    sent = ns.calls["stream"][0]["messages"][-1]["content"]
    assert sent[0]["type"] == "search_result"
    assert sent[0]["source"].startswith("kb://")
    assert sent[-1] == {"type": "text", "text": "Qual o prazo?"}
    assert kinds[-1] == "done"
    await adb(run.refresh_from_db)()
    assert run.state == "done"


@needs_model
async def test_sem_termos_abstem_sem_chamada_paga(setup):
    user, conv, kb, job, ver, p = setup
    assert await adb(process_job)(job.uuid, "w", file_map={str(ver.uuid): p}) == "ready"
    await adb(ConversationKnowledge.objects.create)(
        conversation=conv, bases=[str(kb.uuid)], mode="always"
    )
    run, _, _ = await _reserve(user, conv, "??? !!!")
    client, ns = _client_ok()
    events = [e async for e in execute_run(str(run.uuid), user=user, client=client)]
    assert events[0]["rag"] == {"status": "abstain", "reason": "no_evidence"}
    assert ns.calls["stream"] == [] and ns.calls["create"] == []
    done = events[-1]
    assert done["type"] == "done"
    assert "Não encontrei evidências suficientes" in done["text"]
    await adb(run.refresh_from_db)()
    assert run.state == "done"
    text = await adb(lambda: run.assistant_message.text)()
    assert "Não encontrei" in text


@needs_model
async def test_suporte_fraco_prossegue_e_registra_diagnostico(setup):
    """Calibração §18: sims de sem-resposta sobrepõem respondíveis; sem
    limiar arbitrário, o modelo adjudica com o diagnóstico registrado."""
    user, conv, kb, job, ver, p = setup
    assert await adb(process_job)(job.uuid, "w", file_map={str(ver.uuid): p}) == "ready"
    await adb(ConversationKnowledge.objects.create)(
        conversation=conv, bases=[str(kb.uuid)], mode="always"
    )
    run, _, _ = await _reserve(user, conv, "física quântica?")
    client, ns = _client_ok("Não há evidências sobre isso nas fontes.")
    events = [e async for e in execute_run(str(run.uuid), user=user, client=client)]
    assert events[0]["rag"]["status"] == "ready"
    assert ns.calls["stream"], "com candidatos, o modelo adjudica"
    await adb(run.refresh_from_db)()
    assert run.snapshot["rag"]["diagnosis"]["lexical_support"] == 0


async def test_sem_selecao_comportamento_anterior(user, conversation):
    run, _, _ = await _reserve(user, conversation, "oi?")
    client, ns = _client_ok("Olá!")
    events = [e async for e in execute_run(str(run.uuid), user=user, client=client)]
    assert events[0]["rag"] == {"status": "skipped"}
    assert ns.calls["stream"]
