"""Publicação atômica de versão indexada (RAG-04/RAG-05, M2).

Só publica após validar fragmentos, vetores, FTS e hashes. Troca da
versão ativa em transação curta. Versões antigas mantêm chunks (histórico),
mas só a ativa fica no FTS e na elegibilidade de busca (M3).
"""

from __future__ import annotations

from django.db import transaction

from chat.models_rag import Chunk, ChunkEmbedding, DocumentVersion, EmbeddingProfile
from chat.services.rag import embed, fts

MAX_CHUNKS_PER_DOC = 5000
MAX_CHUNKS_PER_USER = 50000


class PublishError(ValueError):
    """Falha de validação/publicação com mensagem acionável."""


def get_or_create_current_profile() -> EmbeddingProfile:
    profile, _ = EmbeddingProfile.objects.get_or_create(
        **embed.current_profile_kwargs()
    )
    return profile


def check_profile_compatible(base, profile: EmbeddingProfile) -> None:
    active = base.active_profile
    if active is not None and active.pk != profile.pk:
        raise PublishError(
            f"Base usa o perfil {active}; versão indexada com {profile}. "
            "Reindexe a base para o perfil atual antes de publicar."
        )


def check_quotas(owner, new_chunks: int) -> None:
    if new_chunks > MAX_CHUNKS_PER_DOC:
        raise PublishError(
            f"Documento excede {MAX_CHUNKS_PER_DOC} fragmentos ({new_chunks})."
        )
    total = Chunk.objects.filter(version__document__owner=owner).count()
    if total + new_chunks > MAX_CHUNKS_PER_USER:
        raise PublishError(
            f"Usuário excederia {MAX_CHUNKS_PER_USER} fragmentos "
            f"(atual {total}, novos {new_chunks})."
        )


def validate_staging(version: DocumentVersion, profile: EmbeddingProfile) -> int:
    """Confere chunks×embeddings×FTS. Retorna nº de chunks. Levanta PublishError."""
    chunk_ids = list(
        Chunk.objects.filter(version=version, profile=profile)
        .order_by("order")
        .values_list("id", flat=True)
    )
    n_emb = ChunkEmbedding.objects.filter(
        chunk_id__in=chunk_ids, profile=profile, dim=embed.EMBED_DIM
    ).count()
    if n_emb != len(chunk_ids):
        raise PublishError(
            f"Embeddings incompletos: {n_emb} de {len(chunk_ids)} fragmentos."
        )
    if fts.count_indexed(chunk_ids) != len(chunk_ids):
        raise PublishError("FTS dessincronizado da versão provisória.")
    return len(chunk_ids)


def publish_version(job, version: DocumentVersion, profile: EmbeddingProfile) -> int:
    """Troca atômica para a versão indexada. Retorna nº de chunks publicados.

    Guarda de geração: o job deve continuar válido (não cancelado/excluído,
    mesmo claimant e generation); caso contrário nada é publicado.
    """
    from chat.models_rag import IngestionJob

    fresh = IngestionJob.objects.select_related(
        "document", "document__base"
    ).get(pk=job.pk)
    doc = fresh.document
    if (
        fresh.state in ("cancelled", "failed")
        or fresh.claimed_by != job.claimed_by
        or fresh.generation != job.generation
    ):
        raise PublishError("Job cancelado/substituído; publicação recusada.")
    check_profile_compatible(doc.base, profile)

    chunk_ids = list(
        Chunk.objects.filter(version=version, profile=profile).values_list(
            "id", flat=True
        )
    )
    old_version_id = doc.active_version_id
    old_ids: list[int] = []
    if old_version_id is not None and old_version_id != version.pk:
        old_ids = list(
            Chunk.objects.filter(version_id=old_version_id).values_list(
                "id", flat=True
            )
        )
    with transaction.atomic():
        if old_ids:
            fts.delete_chunks(old_ids)
        fts.index_chunks(
            [
                (cid, text)
                for cid, text in Chunk.objects.filter(id__in=chunk_ids).values_list(
                    "id", "search_text"
                )
            ]
        )
        if fts.count_indexed(chunk_ids) != len(chunk_ids):
            raise PublishError("FTS não confirmou a indexação da nova versão.")
        base = doc.base
        if base.active_profile_id is None:
            base.active_profile = profile
            base.active_revision += 1
            base.save(update_fields=["active_profile", "active_revision"])
        else:
            base.active_revision += 1
            base.save(update_fields=["active_revision"])
        version.is_staging = False
        version.save(update_fields=["is_staging"])
        doc.active_version = version
        doc.save(update_fields=["active_version"])
    return len(chunk_ids)
