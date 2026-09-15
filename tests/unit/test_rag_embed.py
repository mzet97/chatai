"""M2: embeddings locais — prefixos, normalização, BLOB (RAG-04).

Formato e prefixos sem modelo; codificação real pula sem modelo preparado.
"""

import numpy as np
import pytest

from chat.services.rag import embed

try:
    _PREPARED = embed.is_prepared()
except Exception:
    _PREPARED = False

needs_model = pytest.mark.skipif(not _PREPARED, reason="modelo não preparado")


def test_blob_roundtrip_e_formato():
    rng = np.random.default_rng(42)
    v = rng.standard_normal(embed.EMBED_DIM).astype(np.float32)
    v /= np.linalg.norm(v)
    blob = embed.pack_vector(v)
    assert blob[:4] == (embed.EMBED_DIM).to_bytes(4, "little")
    assert len(blob) == 4 + embed.EMBED_DIM * 4
    back = embed.unpack_vector(blob)
    np.testing.assert_allclose(back, v, rtol=1e-6)


def test_pack_recusa_dimensao_errada_e_sem_normalizar():
    with pytest.raises(ValueError):
        embed.pack_vector(np.ones(128, dtype=np.float32))
    with pytest.raises(ValueError):
        embed.pack_vector(np.ones(embed.EMBED_DIM, dtype=np.float32) * 5)


def test_unpack_recusa_blob_corrompido():
    with pytest.raises(ValueError):
        embed.unpack_vector(b"\x00\x01")
    with pytest.raises(ValueError):
        embed.unpack_vector((999).to_bytes(4, "little") + b"\x00" * 16)


def test_prefixos_nao_duplicam():
    assert embed._with_prefix(["query: oi"], embed.QUERY_PREFIX) == ["query: oi"]
    assert embed._with_prefix(["oi"], embed.QUERY_PREFIX) == ["query: oi"]
    assert embed._with_prefix(["passage: x"], embed.PASSAGE_PREFIX) == ["passage: x"]


def test_modelo_exige_preparo_explicito(monkeypatch):
    monkeypatch.setattr(embed, "is_prepared", lambda: False)
    monkeypatch.setattr(embed, "_model", None)
    with pytest.raises(RuntimeError, match="rag_prepare"):
        embed.get_model()


@needs_model
def test_codificacao_real_dim_norma_e_prefixo():
    vecs = embed.encode_passages(["O prazo é 30 dias.", "A multa é 2%."])
    assert vecs.shape == (2, embed.EMBED_DIM)
    assert vecs.dtype == np.float32
    np.testing.assert_allclose(
        np.linalg.norm(vecs, axis=1), np.ones(2), rtol=1e-3
    )
    q = embed.encode_query("Qual é o prazo?")
    assert q.shape == (embed.EMBED_DIM,)
    # Passagem relevante ~ consulta; irrelevante diverge (sanidade semântica).
    sim_prazo = float(q @ vecs[0])
    assert sim_prazo > float(q @ vecs[1])
    # Revisão fixada registrada.
    assert embed.EMBED_MODEL_REVISION == "614241f622f53c4eeff9890bdc4f31cfecc418b3"
