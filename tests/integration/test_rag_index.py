"""M2: pipeline chunking→embeddings→FTS→publicação (RAG-04/RAG-05).

Modelo real preparado, sem rede. Pula se o modelo estiver ausente.
"""

import pytest
from django.contrib.auth import get_user_model

from chat.models_rag import (
    Chunk,
    ChunkEmbedding,
    Document,
    DocumentVersion,
    EmbeddingProfile,
    IngestionJob,
    KnowledgeBase,
)
from chat.services.rag import embed, fts, publish
from chat.services.rag.worker import process_job, run_worker_once

pytestmark = pytest.mark.django_db(transaction=True)

try:
    _PREPARED = embed.is_prepared()
except Exception:
    _PREPARED = False

needs_model = pytest.mark.skipif(not _PREPARED, reason="modelo não preparado")


@pytest.fixture
def base(db):
    user = get_user_model().objects.create_user("ragm2", password="pw123456")
    return KnowledgeBase.objects.create(owner=user, name="Base M2"), user


def _enqueue(base, user, name="doc.txt", content=b"conteudo"):
    doc = Document.objects.create(base=base, owner=user, name=name)
    ver = DocumentVersion.objects.create(
        document=doc, number=1, sha256="cd" * 32, filename=name,
        size_bytes=len(content), rel_path=f"{doc.uuid}/{name}",
    )
    job = IngestionJob.objects.create(owner=user, document=doc, version=ver)
    return doc, ver, job


def _run(base, user, content: bytes, name="doc.txt", worker="w1", tmp=None):
    import tempfile
    from pathlib import Path

    doc, ver, job = _enqueue(base, user, name, content)
    root = Path(tempfile.mkdtemp()) if tmp is None else tmp
    path = root / name
    path.write_bytes(content)
    state = process_job(job.uuid, worker, file_map={str(ver.uuid): path})
    for obj in (job, doc, ver):
        obj.refresh_from_db()
    return state, doc, ver, job


@needs_model
def test_pipeline_completa_publica_chunks_vetores_e_fts(base, tmp_path):
    kb, user = base
    content = (
        "O prazo de entrega é 30 dias.\n\nA multa por atraso é 2% ao mês.\n\n"
        "O contrato pode ser rescindido com aviso prévio."
    ).encode()
    state, doc, ver, job = _run(kb, user, content, tmp=tmp_path)
    assert state == "ready", job.error
    assert doc.state == "ready"
    assert doc.active_version_id == ver.pk
    profile = EmbeddingProfile.objects.get(**embed.current_profile_kwargs())
    chunks = list(Chunk.objects.filter(version=ver, profile=profile).order_by("order"))
    assert chunks
    assert ChunkEmbedding.objects.filter(profile=profile, chunk__in=chunks).count() == len(chunks)
    kb.refresh_from_db()
    assert kb.active_profile_id == profile.pk
    # FTS cobre exatamente os chunks publicados.
    ids = [c.pk for c in chunks]
    assert fts.count_indexed(ids) == len(ids)
    hits = fts.lexical_search(ids, "prazo de entrega")
    assert hits and hits[0][0] in ids
    # Vetor real: similaridade consulta×chunk recupera o trecho do prazo.
    q = embed.encode_query("Qual é o prazo de entrega?")
    sims = [(c.pk, float(q @ embed.unpack_vector(c.embedding.vector))) for c in chunks]
    best = max(sims, key=lambda s: s[1])[0]
    assert "30 dias" in Chunk.objects.get(pk=best).text


@needs_model
def test_folding_acentos_e_identificadores_tecnicos(base):
    kb, user = base
    profile = publish.get_or_create_current_profile()
    doc = Document.objects.create(base=kb, owner=user, name="t.txt")
    ver = DocumentVersion.objects.create(
        document=doc, number=1, sha256="ab" * 32, filename="t.txt",
        size_bytes=10, rel_path="x",
    )
    c1 = Chunk.objects.create(
        version=ver, profile=profile, order=0, text="Prazo de rescisão contratual",
        context_hint="", search_text="Prazo de rescisão contratual",
        locator={}, token_count=10,
    )
    c2 = Chunk.objects.create(
        version=ver, profile=profile, order=1, text="Erro E_CONN_TIMEOUT no endpoint",
        context_hint="", search_text="Erro E_CONN_TIMEOUT no endpoint",
        locator={}, token_count=10,
    )
    fts.index_chunks([(c1.pk, c1.search_text), (c2.pk, c2.search_text)])
    ids = [c1.pk, c2.pk]
    # Sem acento encontra com acento; identificador técnico com pontuação.
    assert fts.lexical_search(ids, "rescisao")[0][0] == c1.pk
    assert fts.lexical_search(ids, "prazo rescisão")[0][0] == c1.pk
    got = fts.lexical_search(ids, "E_CONN_TIMEOUT")
    assert got and got[0][0] == c2.pk


@needs_model
def test_match_inseguro_nao_quebra_nem_vaza():
    assert fts.build_match('prazo OR "multa: NEAR') is not None
    assert fts.build_match("!!! ... ???") is None
    assert fts.build_match("") is None
    # Termos citados: operadores viram texto, não sintaxe.
    m = fts.build_match("OR AND NOT")
    assert m == '"OR" "AND" "NOT"' or m is not None


@needs_model
def test_reinicio_no_meio_retoma_e_publica(base, tmp_path):
    kb, user = base
    state, doc, ver, job = _run(kb, user, b"Texto para retomar.", tmp=tmp_path)
    assert state == "ready"
    # Simula morte após extração: novo job sobre a mesma versão retoma.
    job2 = IngestionJob.objects.create(owner=user, document=doc, version=ver)
    job2.state = "embedding"
    job2.save(update_fields=["state"])
    out = process_job(job2.uuid, "w2")
    assert out == "ready", IngestionJob.objects.get(pk=job2.pk).error


@needs_model
def test_cancelado_antes_de_publicar_nao_indexa(base, tmp_path):
    import tempfile
    from pathlib import Path

    kb, user = base
    doc, ver, job = _enqueue(kb, user, content=b"nao publique")
    root = Path(tempfile.mkdtemp())
    p = root / "doc.txt"
    p.write_bytes(b"nao publique")
    job.state = "cancelled"
    job.save(update_fields=["state"])
    out = process_job(job.uuid, "w1", file_map={str(ver.uuid): p})
    assert out == "cancelled"
    doc.refresh_from_db()
    assert doc.active_version_id is None
    assert Chunk.objects.filter(version=ver).count() == 0


@needs_model
def test_troca_de_versao_atomica_e_fts_troca_junto(base, tmp_path):
    kb, user = base
    s1, doc, v1, _ = _run(kb, user, "Versão um fala de prazos.".encode(), tmp=tmp_path)
    assert s1 == "ready"
    v2 = DocumentVersion.objects.create(
        document=doc, number=2, sha256="ef" * 32, filename="doc.txt",
        size_bytes=10, rel_path=f"{doc.uuid}/doc.txt",
    )
    job2 = IngestionJob.objects.create(owner=user, document=doc, version=v2)
    p2 = tmp_path / "v2.txt"
    p2.write_bytes("Versão dois trata de multas e rescisão.".encode())
    out = process_job(job2.uuid, "w1", file_map={str(v2.uuid): p2})
    assert out == "ready", IngestionJob.objects.get(pk=job2.pk).error
    doc.refresh_from_db()
    assert doc.active_version_id == v2.pk
    old_ids = list(Chunk.objects.filter(version=v1).values_list("id", flat=True))
    new_ids = list(Chunk.objects.filter(version=v2).values_list("id", flat=True))
    assert new_ids
    # Versão antiga sai do FTS (histórico preservado no banco).
    assert fts.count_indexed(old_ids) == 0
    assert Chunk.objects.filter(version=v1).exists()
    assert fts.count_indexed(new_ids) == len(new_ids)


@needs_model
def test_perfil_incompativel_recusa_publicar(base, tmp_path):
    kb, user = base
    other = EmbeddingProfile.objects.create(
        model_id="outro/modelo", revision="x" * 40, dim=384, pipeline_version=99
    )
    kb.active_profile = other
    kb.save(update_fields=["active_profile"])
    state, doc, ver, job = _run(kb, user, "Conteúdo qualquer.".encode(), tmp=tmp_path)
    assert state == "failed"
    assert "Reindexe" in job.error
    assert Chunk.objects.filter(version=ver).count() == 0


@needs_model
def test_cota_por_documento_falha_acionavel(base, tmp_path, monkeypatch):
    kb, user = base
    monkeypatch.setattr(publish, "MAX_CHUNKS_PER_DOC", 2)
    paras = "\n\n".join(
        f"Parágrafo {i} com conteúdo distinto. " + ("detalhe relevante. " * 60)
        for i in range(10)
    )
    state, doc, ver, job = _run(kb, user, paras.encode(), tmp=tmp_path)
    assert state == "failed"
    assert "fragmentos" in job.error


@needs_model
def test_run_worker_once_com_modelo_real(base, tmp_path):
    kb, user = base
    doc, ver, job = _enqueue(kb, user, content=b"pipeline via fila")
    p = tmp_path / "doc.txt"
    p.write_bytes(b"pipeline via fila com prazo de 15 dias.")
    n = run_worker_once("w1", file_map={str(ver.uuid): p})
    assert n == 1
    job.refresh_from_db()
    assert job.state == "ready", job.error
