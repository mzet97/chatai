"""Embeddings locais E5 (RAG-04). CPU baseline, carga única por processo.

Modelo: intfloat/multilingual-e5-small, revisão fixada, trust_remote_code=False.
Prefixos `query: `/`passage: ` aplicados aqui (sem duplicar). Vetores
normalizados float32; BLOB = dim uint32 LE + float32 LE (sem pickle).
"""

from __future__ import annotations

import struct
import threading
from pathlib import Path

import numpy as np

EMBED_MODEL_ID = "intfloat/multilingual-e5-small"
EMBED_MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
EMBED_DIM = 384
EMBED_PIPELINE_VERSION = 1
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "
EMBED_BATCH_SIZE = 32

_model = None
_tokenizer = None
_device_note = ""
_lock = threading.Lock()


def model_dir() -> Path:
    from django.conf import settings

    return Path(settings.BASE_DIR) / "models" / "multilingual-e5-small"


def is_prepared() -> bool:
    d = model_dir()
    return (d / "model.safetensors").exists() and (
        d / ".revision"
    ).exists() and (d / ".revision").read_text().strip() == EMBED_MODEL_REVISION


def get_model():
    """Singleton por processo. Levanta RuntimeError acionável se ausente."""
    global _model, _device_note
    if _model is not None:
        return _model
    with _lock:
        if _model is not None:
            return _model
        if not is_prepared():
            raise RuntimeError(
                "Modelo de embeddings não preparado. Rode: "
                "python manage.py rag_prepare"
            )
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(
            str(model_dir()), device="cpu", trust_remote_code=False
        )
        _device_note = "cpu"
        return _model


def get_tokenizer_encode():
    """Encode leve (sem pesos) para o chunking. Offline após rag_prepare."""
    global _tokenizer
    if _tokenizer is not None:
        return _tokenizer.encode
    with _lock:
        if _tokenizer is not None:
            return _tokenizer.encode
        if not is_prepared():
            raise RuntimeError(
                "Modelo de embeddings não preparado. Rode: "
                "python manage.py rag_prepare"
            )
        from transformers import AutoTokenizer

        _tokenizer = AutoTokenizer.from_pretrained(
            str(model_dir()), trust_remote_code=False, local_files_only=True
        )
        return _tokenizer.encode


def device_note() -> str:
    get_model()
    return _device_note


def _with_prefix(texts: list[str], prefix: str) -> list[str]:
    return [t if t.startswith(prefix) else prefix + t for t in texts]


def encode_passages(texts: list[str]) -> np.ndarray:
    """(n, 384) float32 normalizado. Fora do event loop HTTP (worker)."""
    model = get_model()
    vecs = model.encode(
        _with_prefix(list(texts), PASSAGE_PREFIX),
        batch_size=EMBED_BATCH_SIZE,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return np.asarray(vecs, dtype=np.float32)


def encode_query(text: str) -> np.ndarray:
    """(384,) float32 normalizado para a consulta (M3)."""
    model = get_model()
    vec = model.encode(
        _with_prefix([text], QUERY_PREFIX),
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return np.asarray(vec[0], dtype=np.float32)


def pack_vector(vec: np.ndarray) -> bytes:
    v = np.asarray(vec, dtype=np.float32).reshape(-1)
    if v.shape[0] != EMBED_DIM:
        raise ValueError(f"Dimensão inesperada: {v.shape[0]} (perfil: {EMBED_DIM}).")
    norm = float(np.linalg.norm(v))
    if not 0.99 <= norm <= 1.01:
        raise ValueError(f"Vetor não normalizado (norma {norm:.4f}).")
    return struct.pack("<I", EMBED_DIM) + v.astype("<f4").tobytes()


def unpack_vector(blob: bytes) -> np.ndarray:
    if len(blob) < 4:
        raise ValueError("BLOB de vetor curto demais.")
    (dim,) = struct.unpack("<I", bytes(blob[:4]))
    if dim != EMBED_DIM or len(blob) != 4 + dim * 4:
        raise ValueError(f"BLOB de vetor íntegro? dim={dim} len={len(blob)}.")
    return np.frombuffer(blob, dtype="<f4", count=dim, offset=4).astype(np.float32)


def current_profile_kwargs() -> dict:
    return {
        "model_id": EMBED_MODEL_ID,
        "revision": EMBED_MODEL_REVISION,
        "dim": EMBED_DIM,
        "pipeline_version": EMBED_PIPELINE_VERSION,
    }
