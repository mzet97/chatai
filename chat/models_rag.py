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
            models.UniqueConstraint(fields=["owner", "name"], name="rag_base_owner_name_unique")
        ]

    def __str__(self):
        return f"{self.name} ({self.owner})"


class Document(models.Model):
    STATES = ("upload", "processing", "ready", "partial", "needs_ocr", "failed")

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    base = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name="documents")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rag_docs"
    )
    name = models.CharField(max_length=255)  # nome exibido, sanitizado
    state = models.CharField(max_length=12, choices=[(s, s) for s in STATES], default="upload")
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
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="versions")
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
            models.UniqueConstraint(fields=["document", "number"], name="rag_doc_version_unique")
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
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="jobs")
    version = models.ForeignKey(DocumentVersion, on_delete=models.CASCADE, related_name="jobs")
    state = models.CharField(max_length=12, choices=[(s, s) for s in STATES], default="queued")
    attempt = models.PositiveIntegerField(default=0)
    generation = models.PositiveIntegerField(default=0)  # controle anti-publicação
    claimed_by = models.CharField(max_length=120, default="")
    lease_until = models.DateTimeField(null=True, blank=True)
    checkpoint = models.JSONField(default=dict)
    error = models.CharField(max_length=500, default="")
    # Idempotência de upload: mesma (owner, client_key) devolve o job existente.
    client_key = models.CharField(max_length=64, default="", db_index=True)
    progress_known = models.PositiveIntegerField(default=0)
    progress_total = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["state", "lease_until"])]


class OutboxMessage(models.Model):
    """M4: outbox de ingestão (§15). Job + mensagem na mesma transação.

    O dispatcher publica com confirm e só então marca `delivered`;
    confirmar entrega ao broker não comprova processamento — o consumidor
    é idempotente por job/versão e tolera duplicatas. Estados: pending,
    delivering (reivindicada), delivered, failed (após MAX_ATTEMPTS).
    """

    TOPICS = ("ingest.requested",)

    MAX_ATTEMPTS = 25

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    topic = models.CharField(max_length=40)
    # Chave de idempotência: uma mensagem por operação (ex.: job uuid).
    key = models.CharField(max_length=100, unique=True)
    payload = models.JSONField(default=dict)  # só IDs, sem binários/segredos
    state = models.CharField(max_length=12, default="pending", db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    claimed_by = models.CharField(max_length=120, default="")
    error = models.CharField(max_length=500, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["state", "updated_at"])]


class Chunk(models.Model):
    """Fragmento citável: texto + dica de busca + localizador (RAG-04)."""

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    version = models.ForeignKey(DocumentVersion, on_delete=models.CASCADE, related_name="chunks")
    profile = models.ForeignKey(EmbeddingProfile, on_delete=models.PROTECT, related_name="chunks")
    order = models.PositiveIntegerField()
    text = models.TextField()  # passagem original citável
    context_hint = models.CharField(max_length=500, default="")  # só busca
    search_text = models.TextField()  # hint + texto (representação de busca)
    locator = models.JSONField(default=dict)
    token_count = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["version", "profile", "order"], name="rag_chunk_unique")
        ]
        indexes = [models.Index(fields=["version", "profile", "order"])]


class RetrievalRun(models.Model):
    """Execução de busca híbrida: consulta, escopo, diagnóstico (RAG-05)."""

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rag_runs"
    )
    query = models.CharField(max_length=2000)  # pergunta original
    effective_query = models.CharField(max_length=2000)  # pesquisada (§8)
    bases = models.JSONField(default=list)  # uuids selecionados
    versions = models.JSONField(default=dict)  # base_uuid -> version number
    profile = models.ForeignKey(
        EmbeddingProfile, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    method = models.CharField(max_length=12, default="hybrid")  # hybrid|lexical
    diagnosis = models.JSONField(default=dict)
    generation_run_uuid = models.CharField(max_length=36, default="", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Evidence(models.Model):
    """Fragmento selecionado numa execução: ordem, scores, origem (RAG-05)."""

    run = models.ForeignKey(RetrievalRun, on_delete=models.CASCADE, related_name="evidences")
    chunk = models.ForeignKey(Chunk, on_delete=models.CASCADE, related_name="+")
    order = models.PositiveIntegerField()
    rrf_score = models.FloatField()
    rank_lexical = models.IntegerField(null=True, blank=True)
    rank_vector = models.IntegerField(null=True, blank=True)
    kb_id = models.CharField(max_length=300)  # kb://base/versao/fragmento

    class Meta:
        ordering = ["order"]


class ConversationKnowledge(models.Model):
    """Fontes selecionadas por conversa: bases + modo (RAG-06)."""

    MODES = ("always", "tools")

    conversation = models.OneToOneField(
        "chat.Conversation", on_delete=models.CASCADE, related_name="knowledge"
    )
    bases = models.JSONField(default=list)  # uuids de KnowledgeBase
    mode = models.CharField(max_length=10, choices=[(m, m) for m in MODES], default="always")
    updated_at = models.DateTimeField(auto_now=True)


class Citation(models.Model):
    """Fonte citada verificada contra o manifesto da execução (RAG-07)."""

    run = models.ForeignKey(RetrievalRun, on_delete=models.CASCADE, related_name="citations")
    # SET_NULL: purga remove o chunk, mas a citação histórica permanece como
    # tombstone (indisponível, nunca ressuscitada) — RAG-09 / M6.
    chunk = models.ForeignKey(Chunk, on_delete=models.SET_NULL, null=True, related_name="+")
    kb_id = models.CharField(max_length=300)
    source_index = models.PositiveIntegerField()  # [n] exibido na UI
    locator_snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class SourceDependency(models.Model):
    """Dependência citação → versão do chunk (retenção/revogação, RAG-09)."""

    citation = models.ForeignKey(Citation, on_delete=models.CASCADE, related_name="dependencies")
    chunk = models.ForeignKey(Chunk, on_delete=models.SET_NULL, null=True, related_name="+")
    base_uuid = models.CharField(max_length=36)
    version_number = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)


class ChunkEmbedding(models.Model):
    """Vetor float32 normalizado em BLOB: dim uint32 LE + float32 LE (RAG-04)."""

    chunk = models.OneToOneField(Chunk, on_delete=models.CASCADE, related_name="embedding")
    profile = models.ForeignKey(
        EmbeddingProfile, on_delete=models.PROTECT, related_name="embeddings"
    )
    vector = models.BinaryField()
    dim = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
