"""M6: revogação e retenção — purga invalida sem ressuscitar (RAG-09)."""

import pytest

from chat.models import GenerationRun, Message
from chat.models_rag import (
    Chunk,
    Citation,
    Document,
    DocumentVersion,
    IngestionJob,
    KnowledgeBase,
    RetrievalRun,
    SourceDependency,
)
from chat.services.rag import embed, retention
from chat.services.rag.worker import process_job

pytestmark = pytest.mark.django_db(transaction=True)

try:
    _PREPARED = embed.is_prepared()
except Exception:
    _PREPARED = False

needs_model = pytest.mark.skipif(not _PREPARED, reason="modelo não preparado")


@pytest.fixture
def cited(user, conversation, tmp_path):
    """Base indexada + run com citação e dependência, ligada à conversa."""
    kb = KnowledgeBase.objects.create(owner=user, name="Manuais")
    doc = Document.objects.create(base=kb, owner=user, name="prazo.txt")
    content = "O prazo de entrega é 30 dias contados do aceite.".encode()
    ver = DocumentVersion.objects.create(
        document=doc, number=1, sha256="a" * 64, filename="prazo.txt",
        size_bytes=len(content), rel_path=f"{doc.uuid}/prazo.txt",
    )
    job = IngestionJob.objects.create(owner=user, document=doc, version=ver)
    p = tmp_path / "prazo.txt"
    p.write_bytes(content)
    assert process_job(job.uuid, "w", file_map={str(ver.uuid): p}) == "ready"
    chunk = Chunk.objects.filter(version=ver).order_by("order").first()
    assert chunk is not None
    run = RetrievalRun.objects.create(
        owner=user, query="Qual o prazo?", effective_query="Qual o prazo?",
        bases=[str(kb.uuid)],
    )
    cit = Citation.objects.create(
        run=run, chunk=chunk, kb_id=f"kb://{kb.uuid}/1/{chunk.uuid}",
        source_index=1, locator_snapshot={"doc": doc.name},
    )
    SourceDependency.objects.create(
        citation=cit, chunk=chunk, base_uuid=str(kb.uuid), version_number=1,
    )
    um = Message.objects.create(conversation=conversation, seq=1, role="user")
    am = Message.objects.create(conversation=conversation, seq=2, role="assistant")
    GenerationRun.objects.create(
        conversation=conversation, user_message=um, assistant_message=am,
        idempotency_key="k-m6-1", content_hash="h",
        snapshot={"rag": {"retrieval_run_id": run.id}},
    )
    return kb, doc, cit


def test_purge_mantem_citacao_como_tombstone(cited):
    _, doc, cit = cited
    retention.purge_document(doc)
    cit.refresh_from_db()
    assert cit.chunk_id is None  # histórico preservado, sem conteúdo
    assert SourceDependency.objects.filter(citation=cit).exists()
    assert Chunk.objects.count() == 0


def test_citacoes_marcam_fonte_purgada_indisponivel(logged_client, conversation, cited):
    _, doc, _ = cited
    retention.purge_document(doc)
    resp = logged_client.get(f"/api/rag/conversations/{conversation.uuid}/citations")
    assert resp.status_code == 200
    entries = [e for lst in resp.json()["citations"].values() for e in lst]
    assert len(entries) == 1
    assert entries[0]["available"] is False
    assert "excerpt" not in entries[0]


@needs_model
def test_purge_remove_doc_da_recuperacao(cited):
    from chat.services.rag import retrieval

    kb, doc, _ = cited
    user = doc.owner
    before = retrieval.retrieve(user.pk, [str(kb.uuid)], "Qual é o prazo de entrega?")
    assert before.evidences
    retention.purge_document(doc)
    after = retrieval.retrieve(user.pk, [str(kb.uuid)], "Qual é o prazo de entrega?")
    assert not after.evidences
