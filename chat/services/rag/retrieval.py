"""Busca híbrida — RetrievalService único (RAG-05, M3).

Autorização e seleção de versões ANTES de produzir candidatos, nos dois
pilares. 30 candidatos/pilar, RRF k=60 pesos iguais, desempate
determinístico, até 6 finais com redução de sobreposição. Scores de
ranking nunca são exibidos como probabilidade.
"""

from __future__ import annotations

import heapq
import time
from dataclasses import dataclass, field

import numpy as np

from chat.models_rag import Chunk, ChunkEmbedding, KnowledgeBase
from chat.services.rag import embed, fts

PER_PATH_K = 30
RRF_K = 60
FINAL_K = 6
OVERLAP_JACCARD_SKIP = 0.8
VECTOR_BATCH = 2000


class RetrievalError(RuntimeError):
    """Erro acionável de recuperação (sem modelo, sem FTS, sem perfil)."""


class ProfileMismatch(ValueError):
    """Bases com perfis incompatíveis na mesma consulta (RAG-04)."""


@dataclass
class EvidenceHit:
    chunk_id: int
    chunk_uuid: str
    base_uuid: str
    base_name: str
    doc_name: str
    version_number: int
    text: str
    locator: dict
    rrf_score: float
    rank_lexical: int | None
    rank_vector: int | None

    @property
    def kb_id(self) -> str:
        return f"kb://{self.base_uuid}/v{self.version_number}/{self.chunk_uuid}"


@dataclass
class RetrievalResult:
    query: str
    effective_query: str
    evidences: list[EvidenceHit] = field(default_factory=list)
    diagnosis: dict = field(default_factory=dict)
    run_id: int | None = None


def eligible_scope(user_id: int, base_uuids: list[str]):
    """Retorna (chunks qs, profile_id, bases). Ponto único de autorização."""
    bases = list(
        KnowledgeBase.objects.filter(owner_id=user_id, uuid__in=base_uuids)
        .select_related("active_profile")
        .order_by("uuid")
    )
    found = {str(b.uuid) for b in bases}
    missing = [u for u in base_uuids if u not in found]
    if missing:
        raise RetrievalError(f"Bases inacessíveis ou inexistentes: {len(missing)}.")
    profiles = {b.active_profile_id for b in bases if b.active_profile_id}
    if len(profiles) > 1:
        raise ProfileMismatch(
            "Bases com perfis de embeddings incompatíveis. "
            "Reindexe para unificar o perfil antes de combiná-las."
        )
    if not profiles:
        raise RetrievalError("Nenhuma base selecionada possui versão publicada.")
    profile_id = next(iter(profiles))
    # Chunks da VERSÃO ATIVA de cada documento (nunca staging, nunca alheio).
    from chat.models_rag import Document

    active_ids = (
        Document.objects.filter(base__in=bases, active_version__isnull=False)
        .values_list("active_version_id", flat=True)
        .distinct()
    )
    chunks = (
        Chunk.objects.filter(version_id__in=active_ids, profile_id=profile_id)
        .select_related("version", "version__document", "version__document__base")
        .order_by("id")
    )
    return chunks, profile_id, bases


def rrf_fuse(
    lexical: list[tuple[int, float]],
    vector: list[tuple[int, float]],
    k: int = RRF_K,
) -> list[tuple[int, float, int | None, int | None]]:
    """Funde rankings por posição. Retorna (id, score, rank_lex, rank_vec).

    Omitido de uma lista não recebe contribuição dela. Desempate por id.
    """
    scores: dict[int, list] = {}
    for rank, (cid, _) in enumerate(lexical, start=1):
        entry = scores.setdefault(cid, [0.0, None, None])
        entry[0] += 1.0 / (k + rank)
        entry[1] = rank
    for rank, (cid, _) in enumerate(vector, start=1):
        entry = scores.setdefault(cid, [0.0, None, None])
        entry[0] += 1.0 / (k + rank)
        entry[2] = rank
    return sorted(
        ((cid, s, rl, rv) for cid, (s, rl, rv) in scores.items()),
        key=lambda t: (-t[1], t[0]),
    )


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(a.lower().split()), set(b.lower().split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _vector_candidates(
    chunk_ids: list[int], query_vec: np.ndarray, limit: int
) -> list[tuple[int, float]]:
    """Cosseno exato sobre elegíveis, top-k incremental por lotes."""
    heap: list[tuple[float, int]] = []
    for start in range(0, len(chunk_ids), VECTOR_BATCH):
        batch = chunk_ids[start : start + VECTOR_BATCH]
        rows = ChunkEmbedding.objects.filter(chunk_id__in=batch).values_list(
            "chunk_id", "vector"
        )
        for cid, blob in rows:
            sim = float(query_vec @ embed.unpack_vector(blob))
            entry = (sim, cid)
            if len(heap) < limit:
                heapq.heappush(heap, entry)
            elif sim > heap[0][0]:
                heapq.heapreplace(heap, entry)
    # Ordena por similaridade desc; desempate por id (determinístico).
    return [(cid, sim) for sim, cid in sorted(heap, key=lambda t: (-t[0], t[1]))]


def retrieve(
    user_id: int,
    base_uuids: list[str],
    query: str,
    *,
    effective_query: str | None = None,
    method: str = "hybrid",
    persist: bool = True,
) -> RetrievalResult:
    """Busca autorizada. `method`: hybrid | lexical | vector (rotulados)."""
    from chat.models_rag import Evidence, RetrievalRun

    t0 = time.perf_counter()
    eff = effective_query or query
    if not base_uuids:
        return RetrievalResult(
            query=query,
            effective_query=eff,
            diagnosis={"reason": "no_bases", "method": method},
        )
    chunks_qs, profile_id, bases = eligible_scope(user_id, base_uuids)
    chunk_ids = list(chunks_qs.values_list("id", flat=True))
    diag: dict = {
        "method": method,
        "bases": len(bases),
        "eligible_chunks": len(chunk_ids),
        "profile_id": profile_id,
    }
    if not chunk_ids:
        return RetrievalResult(
            query=query, effective_query=eff, diagnosis={**diag, "reason": "empty"}
        )
    if method != "vector" and fts.build_match(eff) is None:
        # Sem termos pesquisáveis: top-k vetorial seria ruído; abster-se.
        return RetrievalResult(
            query=query, effective_query=eff, diagnosis={**diag, "reason": "no_terms"}
        )
    t_scope = time.perf_counter()

    lex = fts.lexical_search(chunk_ids, eff, PER_PATH_K) if method != "vector" else []
    diag["lexical_candidates"] = len(lex)
    vec: list[tuple[int, float]] = []
    if method in ("hybrid", "vector"):
        try:
            qvec = embed.encode_query(eff)
        except RuntimeError as exc:
            raise RetrievalError(str(exc)) from exc
        vec = _vector_candidates(chunk_ids, qvec, PER_PATH_K)
    diag["vector_candidates"] = len(vec)
    t_paths = time.perf_counter()

    fused = rrf_fuse(lex, vec)
    by_id = {c.id: c for c in chunks_qs}
    hits: list[EvidenceHit] = []
    for cid, score, rl, rv in fused:
        c = by_id.get(cid)
        if c is None:  # correu com elegibilidade (não deve ocorrer)
            continue
        if any(_jaccard(c.text, h.text) >= OVERLAP_JACCARD_SKIP for h in hits):
            diag["dedup_skipped"] = diag.get("dedup_skipped", 0) + 1
            continue
        doc = c.version.document
        base = doc.base
        hits.append(
            EvidenceHit(
                chunk_id=c.id,
                chunk_uuid=str(c.uuid),
                base_uuid=str(base.uuid),
                base_name=base.name,
                doc_name=doc.name,
                version_number=c.version.number,
                text=c.text,
                locator=c.locator,
                rrf_score=score,
                rank_lexical=rl,
                rank_vector=rv,
            )
        )
        if len(hits) >= FINAL_K:
            break
    diag.update(
        {
            "fused": len(fused),
            "returned": len(hits),
            "best_sim": round(max((s for _, s in vec), default=0.0), 3),
            "lexical_support": sum(1 for h in hits if h.rank_lexical is not None),
            "ms_scope": round((t_scope - t0) * 1000, 1),
            "ms_paths": round((t_paths - t_scope) * 1000, 1),
            "ms_total": round((time.perf_counter() - t0) * 1000, 1),
        }
    )
    result = RetrievalResult(
        query=query, effective_query=eff, evidences=hits, diagnosis=diag
    )
    if persist and hits:
        seen_versions: dict[str, set[int]] = {}
        for c in chunks_qs:
            seen_versions.setdefault(str(c.version.document.base.uuid), set()).add(
                c.version.number
            )
        run = RetrievalRun.objects.create(
            owner_id=user_id,
            query=query[:2000],
            effective_query=eff[:2000],
            bases=[str(b.uuid) for b in bases],
            versions={k: sorted(v) for k, v in seen_versions.items()},
            profile_id=profile_id,
            method=method,
            diagnosis=diag,
        )
        Evidence.objects.bulk_create(
            [
                Evidence(
                    run=run,
                    chunk_id=h.chunk_id,
                    order=i,
                    rrf_score=h.rrf_score,
                    rank_lexical=h.rank_lexical,
                    rank_vector=h.rank_vector,
                    kb_id=h.kb_id,
                )
                for i, h in enumerate(hits)
            ]
        )
        result.run_id = run.pk
    return result
