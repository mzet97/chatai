"""M4: preparação, orçamento, blocos e verificação de citações (RAG-06/07/08)."""

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from chat.models import Conversation
from chat.models_rag import ConversationKnowledge, KnowledgeBase
from chat.services.rag import answer
from chat.services.rag.answer import ABSTAIN_NO_EVIDENCE, RAG_BUDGET_CHARS

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def conv(db):
    user = get_user_model().objects.create_user("a4u", password="pw123456")
    c = Conversation.objects.create(owner=user, title="t")
    kb = KnowledgeBase.objects.create(owner=user, name="B")
    return user, c, kb


def test_sem_selecao_pula_sem_chamar_nada(conv):
    user, c, _ = conv
    prep = answer.prepare(user.pk, c, "algo?")
    assert prep.status == "skipped"


def test_base_vazia_abstem_sem_custo(conv):
    user, c, kb = conv
    ConversationKnowledge.objects.create(
        conversation=c, bases=[str(kb.uuid)], mode="always"
    )
    prep = answer.prepare(user.pk, c, "algo?")
    assert prep.status == "abstain" and prep.reason == "empty"
    assert prep.message  # texto local, sem geração paga


def test_modo_tools_nao_dispara_pre_busca(conv):
    user, c, kb = conv
    ConversationKnowledge.objects.create(
        conversation=c, bases=[str(kb.uuid)], mode="tools"
    )
    prep = answer.prepare(user.pk, c, "algo?")
    assert prep.status == "skipped"


def test_orcamento_corta_menos_relevantes_e_registra():
    from chat.services.rag.retrieval import EvidenceHit

    hits = [
        EvidenceHit(
            chunk_id=i, chunk_uuid=f"c{i}", base_uuid="b", base_name="B",
            doc_name="d", version_number=1, text="x" * 5000, locator={},
            rrf_score=1.0 / (i + 1), rank_lexical=None, rank_vector=i + 1,
        )
        for i in range(5)
    ]
    # Simula prepare com monkeypatch do retrieve.
    import chat.services.rag.answer as mod

    class FakeRes:
        evidences = hits
        effective_query = "q"
        diagnosis = {}
        run_id = 1

    orig = mod.retrieval.retrieve
    mod.retrieval.retrieve = lambda *a, **k: FakeRes()
    try:
        user = get_user_model().objects.create_user("a4b", password="pw123456")
        c = Conversation.objects.create(owner=user, title="t")
        kb = KnowledgeBase.objects.create(owner=user, name="B2")
        from chat.models_rag import Document

        Document.objects.create(base=kb, owner=user, name="d", state="ready")
        ConversationKnowledge.objects.create(
            conversation=c, bases=[str(kb.uuid)], mode="always"
        )
        prep = answer.prepare(user.pk, c, "q")
    finally:
        mod.retrieval.retrieve = orig
    assert prep.status == "ready"
    assert prep.sent_chars <= RAG_BUDGET_CHARS
    assert prep.dropped >= 1
    assert len(prep.evidences) < len(hits)


def test_blocos_nativos_pergunta_uma_vez():
    from chat.services.rag.retrieval import EvidenceHit

    h = EvidenceHit(
        chunk_id=1, chunk_uuid="cc", base_uuid="bb", base_name="B", doc_name="d.txt",
        version_number=2, text="trecho", locator={"page": 1}, rrf_score=0.1,
        rank_lexical=1, rank_vector=2,
    )
    blocks = answer.build_user_content([h], "pergunta?")
    assert blocks[0]["type"] == "search_result"
    assert blocks[0]["source"] == "kb://bb/v2/cc"
    assert blocks[0]["title"] == "d.txt (v2)"
    assert blocks[-1] == {"type": "text", "text": "pergunta?"}
    assert sum(1 for b in blocks if b["type"] == "text") == 1


def _final_with(cites):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="resp", citations=cites)]
    )


def test_citacao_valida_por_indice_e_trecho():
    from chat.services.rag.retrieval import EvidenceHit

    h = EvidenceHit(
        chunk_id=7, chunk_uuid="cc7", base_uuid="bb", base_name="B", doc_name="d",
        version_number=1, text="O prazo é 30 dias corridos.", locator={},
        rrf_score=0.2, rank_lexical=None, rank_vector=1,
    )
    ok, bad = answer.verify_citations(
        _final_with([{"cited_text": "prazo é 30 dias", "search_result_index": 0}]), [h]
    )
    assert len(ok) == 1 and ok[0]["kb_id"] == "kb://bb/v1/cc7" and not bad
    # Sem índice: casa pelo trecho (único).
    ok2, _ = answer.verify_citations(
        _final_with([{"cited_text": "30 dias corridos"}]), [h]
    )
    assert len(ok2) == 1


def test_citacao_forjada_ou_ambigua_e_invalida():
    from chat.services.rag.retrieval import EvidenceHit

    def mk(i, t):
        return EvidenceHit(
            chunk_id=i, chunk_uuid=f"c{i}", base_uuid="b", base_name="B",
            doc_name="d", version_number=1, text=t, locator={}, rrf_score=0.1,
            rank_lexical=None, rank_vector=1,
        )
    h1, h2 = mk(1, "texto comum aqui"), mk(2, "texto comum ali")
    # Índice fora do manifesto.
    _, bad = answer.verify_citations(
        _final_with([{"cited_text": "x", "search_result_index": 9}]), [h1]
    )
    assert len(bad) == 1
    # Trecho em dois chunks: ambíguo, sem associação por aproximação.
    _, bad2 = answer.verify_citations(
        _final_with([{"cited_text": "texto comum"}]), [h1, h2]
    )
    assert len(bad2) == 1
    # Trecho inexistente.
    _, bad3 = answer.verify_citations(
        _final_with([{"cited_text": "segredo inventado"}]), [h1]
    )
    assert len(bad3) == 1


def test_abstencao_nao_diz_que_nao_existe():
    assert "empresa" in ABSTAIN_NO_EVIDENCE or "fontes selecionadas" in ABSTAIN_NO_EVIDENCE
    assert "não existe" not in ABSTAIN_NO_EVIDENCE
