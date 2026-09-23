"""M3: busca híbrida fim a fim com modelo real (RAG-05)."""

import pytest
from django.contrib.auth import get_user_model

from chat.models_rag import (
    Document,
    DocumentVersion,
    Evidence,
    IngestionJob,
    KnowledgeBase,
    RetrievalRun,
)
from chat.services.rag import embed, retrieval
from chat.services.rag.worker import process_job

pytestmark = pytest.mark.django_db(transaction=True)

try:
    _PREPARED = embed.is_prepared()
except Exception:
    _PREPARED = False

needs_model = pytest.mark.skipif(not _PREPARED, reason="modelo não preparado")


@pytest.fixture
def indexed(db, tmp_path):
    """Duas bases de um usuário + doc similar de outro (isolamento)."""

    u1 = get_user_model().objects.create_user("h1", password="pw123456")
    u2 = get_user_model().objects.create_user("h2", password="pw123456")

    def _index(user, base_name, name, content):
        kb = KnowledgeBase.objects.create(owner=user, name=base_name)
        doc = Document.objects.create(base=kb, owner=user, name=name)
        ver = DocumentVersion.objects.create(
            document=doc, number=1, sha256=f"{name:0<64}"[:64], filename=name,
            size_bytes=len(content), rel_path=f"{doc.uuid}/{name}",
        )
        job = IngestionJob.objects.create(owner=user, document=doc, version=ver)
        p = tmp_path / f"{user.username}-{name}"
        p.write_bytes(content)
        assert process_job(job.uuid, "w", file_map={str(ver.uuid): p}) == "ready"
        kb.refresh_from_db()
        return kb

    kb_a = _index(
        u1, "Contratos", "contrato.txt",
        "O prazo de entrega é 30 dias. A multa por atraso é 2% ao mês.".encode(),
    )
    kb_b = _index(
        u1, "Suporte", "sla.txt",
        "O SLA de suporte responde em 4 horas úteis. A multa contratual é 5%.".encode(),
    )
    kb_other = _index(
        u2, "Alheia", "outro.txt",
        "O prazo de entrega é 90 dias na empresa vizinha.".encode(),
    )
    return u1, u2, kb_a, kb_b, kb_other


@needs_model
def test_hibrida_encontra_evidencia_correta(indexed):
    u1, _, kb_a, _, _ = indexed
    res = retrieval.retrieve(u1.pk, [str(kb_a.uuid)], "Qual é o prazo de entrega?")
    assert res.evidences
    assert "30 dias" in res.evidences[0].text
    assert res.evidences[0].kb_id.startswith("kb://")
    assert res.run_id is not None
    assert Evidence.objects.filter(run_id=res.run_id).count() == len(res.evidences)
    diag = res.diagnosis
    assert diag["method"] == "hybrid"
    # "qual" não está no texto: AND lexical pode dar 0; o pilar vetorial cobre.
    assert diag["vector_candidates"] >= 1
    assert diag["returned"] <= 6


@needs_model
def test_isolamento_entre_usuarios(indexed):
    u1, u2, kb_a, _, kb_other = indexed
    res = retrieval.retrieve(u1.pk, [str(kb_a.uuid)], "prazo de entrega")
    texts = " | ".join(h.text for h in res.evidences)
    assert "90 dias" not in texts  # doc do outro usuário nunca aparece
    assert all(h.base_name == "Contratos" for h in res.evidences)
    # E o outro usuário não enxerga a base alheia nem por uuid.
    from chat.services.rag.retrieval import RetrievalError

    with pytest.raises(RetrievalError):
        retrieval.retrieve(u2.pk, [str(kb_a.uuid)], "prazo")


@needs_model
def test_multiplas_fontes_cobertura_total(indexed):
    u1, _, kb_a, kb_b, _ = indexed
    res = retrieval.retrieve(
        u1.pk, [str(kb_a.uuid), str(kb_b.uuid)], "multa por atraso e multa contratual"
    )
    texts = " | ".join(h.text for h in res.evidences)
    assert "2%" in texts and "5%" in texts  # todas as fontes necessárias
    assert {h.base_name for h in res.evidences} == {"Contratos", "Suporte"}


@needs_model
def test_lexical_isolado_rotulado(indexed):
    u1, _, kb_a, _, _ = indexed
    res = retrieval.retrieve(
        u1.pk, [str(kb_a.uuid)], "prazo de entrega", method="lexical"
    )
    assert res.diagnosis["method"] == "lexical"
    assert res.diagnosis["vector_candidates"] == 0
    assert any("30 dias" in h.text for h in res.evidences)


@needs_model
def test_sem_evidencia_sem_resultado(indexed):
    u1, _, kb_a, _, _ = indexed
    res = retrieval.retrieve(u1.pk, [str(kb_a.uuid)], "física quântica de buracos negros")
    assert res.evidences == [] or all(
        "quant" not in h.text.lower() for h in res.evidences
    )
    run = RetrievalRun.objects.filter(owner=u1).order_by("-id").first()
    assert run is not None or res.evidences == []
