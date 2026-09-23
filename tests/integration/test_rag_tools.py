"""M5: ferramentas locais RAG no loop existente (RAG-12)."""

import pytest
from asgiref.sync import sync_to_async

from chat.models_rag import (
    ConversationKnowledge,
    Document,
    DocumentVersion,
    IngestionJob,
    KnowledgeBase,
)
from chat.models_tools import ConversationToolPrefs
from chat.services.rag import embed as _embed
from chat.services.rag.tools import read_knowledge_excerpt, search_knowledge_base
from chat.services.rag.worker import process_job
from chat.services.tools import catalog as tool_catalog
from chat.services.tools import executor as _executor
from chat.services.tools.context import ExecutionContext

pytestmark = pytest.mark.django_db(transaction=True)
adb = sync_to_async

try:
    _PREPARED = _embed.is_prepared()
except Exception:
    _PREPARED = False

needs_model = pytest.mark.skipif(not _PREPARED, reason="modelo não preparado")


@pytest.fixture
def indexed(db, tmp_path):
    from django.contrib.auth import get_user_model

    from chat.models import Conversation

    user = get_user_model().objects.create_user("t5u", password="pw123456")
    conv = Conversation.objects.create(owner=user, title="t")
    kb = KnowledgeBase.objects.create(owner=user, name="Base")
    doc = Document.objects.create(base=kb, owner=user, name="doc.txt")
    content = "O prazo de entrega é 30 dias corridos.".encode()
    ver = DocumentVersion.objects.create(
        document=doc, number=1, sha256="bb" * 32, filename="doc.txt",
        size_bytes=len(content), rel_path=f"{doc.uuid}/doc.txt",
    )
    job = IngestionJob.objects.create(owner=user, document=doc, version=ver)
    p = tmp_path / "doc.txt"
    p.write_bytes(content)
    assert process_job(job.uuid, "w", file_map={str(ver.uuid): p}) == "ready"
    ConversationKnowledge.objects.create(
        conversation=conv, bases=[str(kb.uuid)], mode="tools"
    )
    return user, conv, kb, doc, ver


def _ctx(user, conv, run="run-1"):
    return ExecutionContext(
        user_id=user.pk, conversation_id=conv.pk, run_uuid=run
    )


@needs_model
async def test_search_respeita_selecao_e_retorna_citavel(indexed):
    user, conv, kb, doc, ver = indexed
    out = await search_knowledge_base({"query": "prazo de entrega"}, _ctx(user, conv))
    assert out["ok"] and "30 dias" in out["text"]
    assert "kb://" in out["text"] and "[1]" in out["text"]


@needs_model
async def test_search_sem_selecao_negada(indexed):
    user, conv, kb, doc, ver = indexed
    await adb(ConversationKnowledge.objects.filter(conversation=conv).delete)()
    out = await search_knowledge_base({"query": "prazo"}, _ctx(user, conv))
    assert not out["ok"] and out["error"]["code"] == "no_sources"


@needs_model
async def test_read_exige_evidencia_da_conversa(indexed):
    from chat.models_rag import Chunk

    user, conv, kb, doc, ver = indexed
    chunk = await adb(Chunk.objects.filter(version=ver).first)()
    out = await read_knowledge_excerpt({"evidence_id": str(chunk.uuid)}, _ctx(user, conv))
    assert out["ok"] and "30 dias" in out["text"]
    # UUID válido de conversa sem seleção não concede acesso (sem oracle).
    from chat.models import Conversation

    conv2 = await adb(Conversation.objects.create)(owner=user, title="outra")
    ctx2 = ExecutionContext(user_id=user.pk, conversation_id=conv2.pk)
    out2 = await read_knowledge_excerpt({"evidence_id": str(chunk.uuid)}, ctx2)
    assert not out2["ok"] and out2["error"]["code"] == "not_found"
    # UUID inexistente: mesmo código (sem oracle).
    out3 = await read_knowledge_excerpt(
        {"evidence_id": "00000000-0000-0000-0000-000000000000"}, _ctx(user, conv)
    )
    assert not out3["ok"] and out3["error"]["code"] == "not_found"


@needs_model
async def test_read_recusa_versao_substituida(indexed):
    from chat.models_rag import Chunk

    user, conv, kb, doc, ver = indexed
    chunk = await adb(Chunk.objects.filter(version=ver).first)()
    ver2 = await adb(DocumentVersion.objects.create)(
        document=doc, number=2, sha256="cc" * 32, filename="doc.txt",
        size_bytes=3, rel_path="x",
    )
    doc.active_version = ver2
    await adb(doc.save)(update_fields=["active_version"])
    out = await read_knowledge_excerpt({"evidence_id": str(chunk.uuid)}, _ctx(user, conv))
    assert not out["ok"] and out["error"]["code"] == "stale"


async def test_catalogo_expoe_tools_rag_sob_prefs(indexed):
    user, conv, kb, doc, ver = indexed
    assert await adb(tool_catalog.authorized_catalog)(conv) == []
    await adb(ConversationToolPrefs.objects.create)(
        conversation=conv, enabled=["local:search_knowledge_base"]
    )
    recs = await adb(tool_catalog.authorized_catalog)(conv)
    assert [r.stable_id for r in recs] == ["local:search_knowledge_base"]
    assert recs[0].approval == "auto"


@needs_model
async def test_executor_respeita_limite_de_bytes(indexed):
    user, conv, kb, doc, ver = indexed
    out = await _executor.execute(
        "local__search_knowledge_base", {"query": "prazo"}, _ctx(user, conv)
    )
    assert out["ok"]
    assert len(out["text"].encode("utf-8")) <= 32 * 1024
