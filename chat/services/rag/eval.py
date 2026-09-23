"""Avaliação de recuperação §18 (M3): corpus versionado, métricas, relatório.

Hit@k: fração com ≥1 marcador esperado no top-k.
Recall@k: média de |achados ∩ esperados| / |esperados| (só respondíveis).
MRR: média de 1/rank do primeiro acerto (rank 1-based no top-k).
Cobertura total (multi): fração com TODOS os esperados no top-k.
Fragmentos sobrepostos equivalentes: marcadores por substring dobrada
(acento-insensível) não inflam — cada marcador conta uma vez.
Sem resposta: não pontuam; reporta quantas retornaram candidatos (M4 abstém).
"""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from pathlib import Path

from chat.services.rag import fts
from chat.services.rag.followup import build_query

K = 6
FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "rag" / "eval"


def load_fixtures() -> tuple[dict, dict]:
    corpus = json.loads((FIXTURES / "corpus.json").read_text())
    questions = json.loads((FIXTURES / "questions.json").read_text())
    return corpus, questions


def _norm(s: str) -> str:
    return fts.fold(s or "").lower()


def score_question(expected: list[str], retrieved_texts: list[str]) -> dict:
    found = []
    for marker in expected:
        m = _norm(marker)
        rank = next(
            (i + 1 for i, t in enumerate(retrieved_texts) if m in _norm(t)), None
        )
        if rank is not None:
            found.append(rank)
    n_exp = len(expected)
    return {
        "hit": bool(found),
        "recall": len(found) / n_exp if n_exp else 0.0,
        "mrr": (1.0 / min(found)) if found else 0.0,
        "full": len(found) == n_exp if n_exp else True,
    }


def run_eval(user, method: str = "hybrid", persist_runs: bool = False) -> dict:
    """Indexa o corpus para `user`, roda as 40 perguntas, limpa tudo."""
    from django.contrib.auth import get_user_model  # noqa
    from chat.models_rag import (
        Chunk,
        Document,
        DocumentVersion,
        IngestionJob,
        KnowledgeBase,
    )
    from chat.services.rag import retrieval
    from chat.services.rag.worker import process_job

    corpus, questions = load_fixtures()
    base_map: dict[str, KnowledgeBase] = {}
    for doc in corpus["documents"]:
        kb = base_map.get(doc["base"])
        if kb is None:
            kb = KnowledgeBase.objects.create(owner=user, name=f"EVAL {doc['base']}")
            base_map[doc["base"]] = kb
        d = Document.objects.create(base=kb, owner=user, name=doc["name"])
        content = doc["content"].encode()
        ver = DocumentVersion.objects.create(
            document=d, number=1, sha256=f"eval-{doc['id']}"[:64].ljust(64, "0"),
            filename=doc["name"], size_bytes=len(content),
            rel_path=f"{d.uuid}/{doc['name']}",
        )
        job = IngestionJob.objects.create(owner=user, document=d, version=ver)
        root = Path(tempfile.mkdtemp())
        p = root / doc["name"]
        p.write_bytes(content)
        state = process_job(job.uuid, "eval", file_map={str(ver.uuid): p})
        if state != "ready":
            raise RuntimeError(f"corpus {doc['id']}: {job.error or state}")

    results = []
    for q in questions["questions"]:
        bases = [base_map[b] for b in q["base"]]
        bq = build_query(q["q"], q.get("last_q"))
        eff = bq.effective if q["type"] == "followup" else q["q"]
        t0 = time.perf_counter()
        res = retrieval.retrieve(
            user.pk, [str(b.uuid) for b in bases], q["q"],
            effective_query=eff, method=method, persist=persist_runs,
        )
        ms = (time.perf_counter() - t0) * 1000
        texts = [h.text for h in res.evidences[:K]]
        entry = {"id": q["id"], "type": q["type"], "ms": round(ms, 1),
                 "returned": len(texts)}
        if q["expected"]:
            entry.update(score_question(q["expected"], texts))
        else:
            entry.update({"unanswerable_returned": len(texts)})
        results.append(entry)

    # Limpeza: FTS primeiro (sem triggers), depois cascata do Django.
    for kb in base_map.values():
        ids = list(
            Chunk.objects.filter(version__document__base=kb).values_list("id", flat=True)
        )
        fts.delete_chunks(ids)
        kb.delete()
    return {"method": method, "results": results}


def summarize(results: list[dict]) -> dict:
    ans = [r for r in results if "hit" in r]
    multi = [r for r in ans if r["type"] == "multi"]
    unans = [r for r in results if "unanswerable_returned" in r]
    ms = sorted(r["ms"] for r in results)

    def pct(p):
        return ms[min(len(ms) - 1, int(p * len(ms)))]
    return {
        "n": len(results),
        "n_answerable": len(ans),
        "hit_at_6": round(sum(r["hit"] for r in ans) / len(ans), 3) if ans else 0.0,
        "recall_at_6": round(statistics.mean([r["recall"] for r in ans]), 3) if ans else 0.0,
        "mrr": round(statistics.mean([r["mrr"] for r in ans]), 3) if ans else 0.0,
        "multi_full_coverage": (
            round(sum(r["full"] for r in multi) / len(multi), 3) if multi else 0.0
        ),
        "unanswerable_with_candidates": sum(1 for r in unans if r["unanswerable_returned"]),
        "ms_mean": round(statistics.mean(ms), 1),
        "ms_p50": round(pct(0.5), 1),
        "ms_p95": round(pct(0.95), 1),
        "failures": [r["id"] for r in ans if not r["hit"]],
    }
