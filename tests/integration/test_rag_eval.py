"""M3: métricas de avaliação e fumaça do corpus (RAG-05, §18)."""

import pytest

from chat.services.rag import embed
from chat.services.rag.eval import load_fixtures, run_eval, score_question, summarize

try:
    _PREPARED = embed.is_prepared()
except Exception:
    _PREPARED = False

needs_model = pytest.mark.skipif(not _PREPARED, reason="modelo não preparado")


def test_corpus_tem_40_perguntas_balanceadas():
    corpus, questions = load_fixtures()
    qs = questions["questions"]
    assert len(qs) == 40
    kinds = {}
    for q in qs:
        kinds[q["type"]] = kinds.get(q["type"], 0) + 1
    assert kinds == {
        "factual": 12, "paraphrase": 8, "crosslang": 4,
        "multi": 4, "followup": 4, "unanswerable": 8,
    }
    assert len(corpus["documents"]) >= 6


def test_metricas_definidas():
    s = score_question(["30 dias", "2%"], ["prazo de 30 dias corridos", "outro"])
    assert s["hit"] and s["recall"] == pytest.approx(0.5)
    assert s["mrr"] == pytest.approx(1.0) and not s["full"]
    s2 = score_question(["ausente"], ["nada a ver"])
    assert not s2["hit"] and s2["recall"] == 0.0 and s2["mrr"] == 0.0


@needs_model
@pytest.mark.django_db(transaction=True)
def test_eval_hibrida_bate_meta_inicial(db):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user("evalt", password="pw123456")
    try:
        out = run_eval(user, method="hybrid")
        s = summarize(out["results"])
        assert s["n"] == 40 and s["n_answerable"] == 32
        assert s["hit_at_6"] >= 0.85, f"falhas: {s['failures']}"
    finally:
        user.delete()
