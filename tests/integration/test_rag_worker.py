"""M1: worker recuperável — fila, lease, cancelamento, reinício (RAG-03)."""

import pytest
from django.contrib.auth import get_user_model

from chat.models_rag import Document, DocumentVersion, IngestionJob, KnowledgeBase
from chat.services.rag.worker import claim_next_job, process_job, run_worker_once

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def base(db):
    user = get_user_model().objects.create_user("ragu", password="pw123456")
    return KnowledgeBase.objects.create(owner=user, name="Manuais"), user


def _enqueue(base, user, name="sample.txt", content=b"conteudo de teste"):
    doc = Document.objects.create(base=base, owner=user, name=name)
    ver = DocumentVersion.objects.create(
        document=doc, number=1, sha256="ab" * 32, filename=name,
        size_bytes=len(content), rel_path=f"{doc.uuid}/{name}",
    )
    job = IngestionJob.objects.create(owner=user, document=doc, version=ver)
    return doc, ver, job


def test_claim_atomico_um_vencedor(base):
    kb, user = base
    _, _, _ = _enqueue(kb, user)
    first = claim_next_job("w1")
    second = claim_next_job("w2")
    assert first is not None and second is None
    assert first.claimed_by == "w1"


def test_cancelado_nunca_publica(base):
    kb, user = base
    doc, ver, job = _enqueue(kb, user)
    claimed = claim_next_job("w1")
    assert claimed.uuid == job.uuid
    job.refresh_from_db()
    job.state = "cancelled"
    job.save(update_fields=["state"])
    process_job(job.uuid, "w1")
    doc.refresh_from_db()
    assert doc.state != "ready"  # worker atrasado não publica
    job.refresh_from_db()
    assert job.state == "cancelled"


def test_reinicio_recupera_lease_expirado(base):
    kb, user = base
    _, _, job = _enqueue(kb, user)
    claimed = claim_next_job("w1")
    assert claimed is not None
    from django.utils import timezone

    IngestionJob.objects.filter(pk=job.pk).update(
        lease_until=timezone.now() - timezone.timedelta(minutes=10)
    )
    reclaimed = claim_next_job("w2")
    assert reclaimed is not None and reclaimed.uuid == job.uuid


def test_run_worker_once_processa_txt(base, tmp_path):
    kb, user = base
    doc, ver, job = _enqueue(kb, user, content="O prazo é 30 dias.".encode())
    path = tmp_path / "sample.txt"
    path.write_bytes("O prazo é 30 dias.".encode())
    processed = run_worker_once("w1", storage_root=tmp_path, file_map={str(ver.uuid): path})
    assert processed == 1
    job.refresh_from_db()
    doc.refresh_from_db()
    assert job.state == "ready"
    assert doc.state == "ready"
    ver.refresh_from_db()
    assert "30 dias" in ver.text
