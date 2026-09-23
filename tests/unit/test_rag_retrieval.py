"""M3: RRF determinístico, follow-up, autorização e isolamento (RAG-05, §8)."""

import pytest
from django.contrib.auth import get_user_model

from chat.models_rag import KnowledgeBase
from chat.services.rag import retrieval
from chat.services.rag.followup import build_query
from chat.services.rag.retrieval import ProfileMismatch, RetrievalError, rrf_fuse


def test_rrf_pesos_iguais_e_deterministicos():
    lex = [(1, -1.0), (2, -2.0)]
    vec = [(2, 0.9), (3, 0.8)]
    fused = rrf_fuse(lex, vec)
    order = [cid for cid, _, _, _ in fused]
    assert order[0] == 2  # presente nas duas listas
    assert rrf_fuse(lex, vec) == fused  # determinístico
    # Omitido de uma lista não recebe contribuição dela.
    solo = [f for f in fused if f[0] == 1][0]
    assert solo[1] == pytest.approx(1.0 / (60 + 1))
    assert solo[2] == 1 and solo[3] is None


def test_rrf_desempate_por_id():
    fused = rrf_fuse([(5, 0.0)], [(3, 0.0)])
    assert [c for c, _, _, _ in fused] == [3, 5]


def test_followup_curta_usa_ultimo_topico():
    bq = build_query("e qual é o prazo?", "Contrato de prestação de serviços")
    assert bq.aux_used
    assert bq.original == "e qual é o prazo?"
    assert "Contrato" in bq.effective
    assert bq.effective.startswith(bq.original)


def test_followup_longa_ignora_contexto():
    q = "Qual é o prazo de entrega previsto na cláusula terceira do contrato?"
    bq = build_query(q, "Outro assunto qualquer aqui")
    assert not bq.aux_used and bq.effective == q


def test_followup_sem_historico():
    bq = build_query("e o valor?", None)
    assert not bq.aux_used and bq.effective == "e o valor?"


@pytest.mark.django_db
def test_sem_bases_retorna_vazio_com_motivo(db):
    user = get_user_model().objects.create_user("nobody", password="pw123456")
    res = retrieval.retrieve(user.pk, [], "algo?")
    assert res.evidences == [] and res.diagnosis["reason"] == "no_bases"


@pytest.mark.django_db
def test_base_de_outro_usuario_e_inacessivel(db):
    from chat.services.rag import publish

    a = get_user_model().objects.create_user("uaua", password="pw123456")
    b = get_user_model().objects.create_user("ubub", password="pw123456")
    kb = KnowledgeBase.objects.create(owner=a, name="Privada")
    kb.active_profile = publish.get_or_create_current_profile()
    kb.save(update_fields=["active_profile"])
    with pytest.raises(RetrievalError):
        retrieval.retrieve(b.pk, [str(kb.uuid)], "algo?")


@pytest.mark.django_db
def test_perfis_incompativeis_recusam_consulta_conjunta(db):
    from chat.models_rag import EmbeddingProfile
    from chat.services.rag import publish

    u = get_user_model().objects.create_user("umum", password="pw123456")
    p1 = publish.get_or_create_current_profile()
    p2 = EmbeddingProfile.objects.create(
        model_id="outro", revision="y" * 40, dim=384, pipeline_version=7
    )
    k1 = KnowledgeBase.objects.create(owner=u, name="A", active_profile=p1)
    k2 = KnowledgeBase.objects.create(owner=u, name="B", active_profile=p2)
    with pytest.raises(ProfileMismatch):
        retrieval.retrieve(u.pk, [str(k1.uuid), str(k2.uuid)], "algo?")
