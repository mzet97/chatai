"""Retenção e purga (RAG-09; M5 usa purga na UI, M6 testa revogação).

"Remover da busca" ≠ "apagar todas as cópias". Purga elimina originais,
extrações, vetores, FTS e jobs da aplicação; mantém apenas
tombstones/metadados não sensíveis (linhas Document marcadas). Citações
antigas permanecem ligadas à versão usada (nunca reapontadas); após purga,
o painel de fonte informa indisponibilidade em vez de ressuscitar.
"""

from __future__ import annotations

import shutil

from django.db import transaction

from chat.models_rag import Chunk, Document
from chat.services.rag import fts
from chat.services.rag.storage import rag_root


def _remove_files(owner_id: int, rel_paths: list[str]) -> None:
    root = rag_root()
    for rel in rel_paths:
        try:
            p = (root / rel).resolve()
            if root.resolve() not in p.parents and p != root.resolve():
                continue
            if p.is_file():
                p.unlink()
        except OSError:
            continue


def purge_document(document: Document) -> dict:
    """Apaga versões, chunks, vetores, FTS, jobs e arquivos de um documento."""
    from chat.models_rag import DocumentVersion, IngestionJob

    versions = list(
        DocumentVersion.objects.filter(document=document).values_list("id", "rel_path")
    )
    chunk_ids = list(
        Chunk.objects.filter(version__document=document).values_list("id", flat=True)
    )
    with transaction.atomic():
        if chunk_ids:
            fts.delete_chunks(chunk_ids)
        IngestionJob.objects.filter(document=document).delete()
        DocumentVersion.objects.filter(document=document).delete()
        _remove_files(document.owner_id, [rel for _, rel in versions if rel])
        name = document.name
        document.delete()
    return {"document": name, "versions": len(versions), "chunks": len(chunk_ids)}


def purge_base(base) -> dict:
    """Apaga documentos da base + diretório; a base some (sem tombstone)."""
    docs = list(Document.objects.filter(base=base))
    out = {"documents": 0, "chunks": 0}
    for doc in docs:
        r = purge_document(doc)
        out["documents"] += 1
        out["chunks"] += r["chunks"]
    base_dir = rag_root() / str(base.owner_id) / str(base.uuid)
    shutil.rmtree(base_dir, ignore_errors=True)
    name = base.name
    base.delete()
    out["base"] = name
    return out
