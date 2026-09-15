"""Entidades RAG (M1: bases, documentos, versões, jobs; M2: chunks, perfis)."""

import uuid

from django.conf import settings
from django.db import models


class EmbeddingProfile(models.Model):
    """Perfil de indexação: modelo/revisão/tokenizer/dimensão/prefixos (RAG-04)."""

    model_id = models.CharField(max_length=200)
    revision = models.CharField(max_length=64)
    dim = models.PositiveIntegerField()
    pipeline_version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["model_id", "revision", "pipeline_version"],
                name="rag_profile_unique",
            )
        ]

    def __str__(self):
        return f"{self.model_id}@{self.revision[:12]} (v{self.pipeline_version})"


class KnowledgeBase(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rag_bases"
    )
    name = models.CharField(max_length=120)
    active_revision = models.PositiveIntegerField(default=0)
    active_profile = models.ForeignKey(
        EmbeddingProfile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name"], name="rag_base_owner_name_unique"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.owner})"


class Document(models.Model):
    STATES = ("upload", "processing", "ready", "partial", "needs_ocr", "failed")

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    base = models.ForeignKey(
        KnowledgeBase, on_delete=models.CASCADE, related_name="documents"
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rag_docs"
    )
    name = models.CharField(max_length=255)  # nome exibido, sanitizado
    state = models.CharField(
        max_length=12, choices=[(s, s) for s in STATES], default="upload"
    )
    active_version = models.ForeignKey(
        "DocumentVersion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["base", "state"])]


class DocumentVersion(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="versions"
    )
    number = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)
    filename = models.CharField(max_length=255)
    size_bytes = models.PositiveIntegerField()
    rel_path = models.CharField(max_length=500)  # dentro do storage privado
    extractor = models.CharField(max_length=120, default="")
    text = models.TextField(default="")  # texto canônico extraído
    locators = models.JSONField(default=dict)  # cobertura: páginas/seções
    warnings = models.JSONField(default=list)
    is_staging = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["document", "number"], name="rag_doc_version_unique"
            )
        ]


class IngestionJob(models.Model):
    STATES = (
        "queued",
        "extracting",
        "chunking",
        "embedding",
        "publishing",
        "ready",
        "cancelled",
        "failed",
        "needs_ocr",
    )

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rag_jobs"
    )
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="jobs"
    )
    version = models.ForeignKey(
        DocumentVersion, on_delete=models.CASCADE, related_name="jobs"
    )
    state = models.CharField(
        max_length=12, choices=[(s, s) for s in STATES], default="queued"
    )
    attempt = models.PositiveIntegerField(default=0)
    generation = models.PositiveIntegerField(default=0)  # controle anti-publicação
    claimed_by = models.CharField(max_length=120, default="")
    lease_until = models.DateTimeField(null=True, blank=True)
    checkpoint = models.JSONField(default=dict)
    error = models.CharField(max_length=500, default="")
    progress_known = models.PositiveIntegerField(default=0)
    progress_total = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["state", "lease_until"])]


class Chunk(models.Model):
    """Fragmento citável: texto + dica de busca + localizador (RAG-04)."""

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    version = models.ForeignKey(
        DocumentVersion, on_delete=models.CASCADE, related_name="chunks"
    )
    profile = models.ForeignKey(
        EmbeddingProfile, on_delete=models.PROTECT, related_name="chunks"
    )
    order = models.PositiveIntegerField()
    text = models.TextField()  # passagem original citável
    context_hint = models.CharField(max_length=500, default="")  # só busca
    search_text = models.TextField()  # hint + texto (representação de busca)
    locator = models.JSONField(default=dict)
    token_count = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["version", "profile", "order"], name="rag_chunk_unique"
            )
        ]
        indexes = [models.Index(fields=["version", "profile", "order"])]


class ChunkEmbedding(models.Model):
    """Vetor float32 normalizado em BLOB: dim uint32 LE + float32 LE (RAG-04)."""

    chunk = models.OneToOneField(
        Chunk, on_delete=models.CASCADE, related_name="embedding"
    )
    profile = models.ForeignKey(
        EmbeddingProfile, on_delete=models.PROTECT, related_name="embeddings"
    )
    vector = models.BinaryField()
    dim = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
