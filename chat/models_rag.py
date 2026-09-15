"""Entidades RAG (M1: bases, documentos, versões, jobs)."""

import uuid

from django.conf import settings
from django.db import models


class KnowledgeBase(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rag_bases"
    )
    name = models.CharField(max_length=120)
    active_revision = models.PositiveIntegerField(default=0)
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
