"""Modelos. Invariantes principais nos testes (docs/tasks.md)."""

import uuid

from django.conf import settings
from django.db import models

from chat.models_agents import (  # noqa: F401 — registra modelos de agentes p/ migrations
    AgentDefinition,
    AgentRun,
    AgentVersion,
    BudgetLedger,
    CacheObservation,
    Delegation,
    RunEvent,
)
from chat.models_rag import (  # noqa: F401 — registra modelos RAG p/ migrations
    Document,
    DocumentVersion,
    IngestionJob,
    KnowledgeBase,
    OutboxMessage,
)
from chat.models_runtime import (  # noqa: F401 — registra diário M2 p/ migrations
    RunJournalEvent,
)
from chat.models_tools import (  # noqa: F401 — registra modelos p/ migrations
    MCPConnection,
    ModelStep,
    StudyNote,
    ToolApproval,
    ToolCatalogSnapshot,
    ToolInvocation,
)

RUN_STATES = (
    "queued",  # M2: comando aceito, aguardando worker (dono vê; aba pode fechar)
    "running",  # M2: reivindicado pelo worker; preparing/streaming são sub-etapas
    "preparing",
    "streaming",
    "awaiting_approval",
    "done",
    "failed",
    "cancelled",
    "interrupted",
    "abandoned",
)
TERMINAL_RUN_STATES = ("done", "failed", "cancelled", "interrupted", "abandoned")
MESSAGE_STATES = ("ok", "partial", "failed", "cancelled")


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class Conversation(models.Model):
    uuid = models.UUIDField(default=new_uuid, unique=True, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversations"
    )
    title = models.CharField(max_length=200)
    archived = models.BooleanField(default=False)
    preferred_model = models.CharField(max_length=200, blank=True, default="")
    system_prompt = models.TextField(blank=True, default="")
    temperature_level = models.CharField(
        max_length=10,
        choices=[("low", "Baixo"), ("medium", "Médio"), ("high", "Alto")],
        default="medium",
    )
    # Pensamento (TV-1): modo default = padrão do modelo, sem efeito até
    # escolha explícita; nível salvo inicia em Médio. Migração não altera
    # valores existentes nem atribui pensamento a respostas antigas.
    thinking_mode = models.CharField(
        max_length=10,
        choices=[
            ("default", "Padrão do modelo"),
            ("disabled", "Desativado"),
            ("enabled", "Ativado"),
        ],
        default="default",
    )
    thinking_level = models.CharField(
        max_length=10,
        choices=[("low", "Baixo"), ("medium", "Médio"), ("high", "Alto")],
        default="medium",
    )
    thinking_budget = models.PositiveIntegerField(default=1024)
    thinking_show_summary = models.BooleanField(default=False)
    # Agentes (AG-1): modo da conversa + perfil selecionado. Chat preserva o
    # comportamento existente; sem perfil, sem delegação.
    agent_mode = models.CharField(
        max_length=10,
        choices=[("chat", "Chat"), ("agent", "Agente"), ("team", "Equipe")],
        default="chat",
    )
    agent_definition = models.ForeignKey(
        "chat.AgentDefinition",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="conversations",
    )
    # Cache (AG-5.1/M2): override da conversa sobre o perfil. "" = sem
    # override (vale a versão do agente → padrões). Validado no PATCH.
    cache_mode = models.CharField(max_length=20, default="", blank=True)
    cache_ttl = models.CharField(max_length=5, default="", blank=True)
    response_mode = models.CharField(
        max_length=10,
        choices=[("streaming", "Streaming"), ("complete", "Completa")],
        default="streaming",
    )
    max_output_tokens = models.PositiveIntegerField(null=True, blank=True)
    input_budget = models.PositiveIntegerField(null=True, blank=True)
    strict_mode = models.BooleanField(default=False)
    active_run = models.ForeignKey(
        "GenerationRun", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["owner", "-updated_at"]),
            models.Index(fields=["owner", "archived"]),
        ]

    def __str__(self) -> str:
        return self.title


class Message(models.Model):
    uuid = models.UUIDField(default=new_uuid, unique=True, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    seq = models.PositiveIntegerField()
    role = models.CharField(max_length=10, choices=[("user", "user"), ("assistant", "assistant")])
    text = models.TextField(default="")
    blocks = models.JSONField(default=list)  # blocos de conteúdo preservados (texto)
    state = models.CharField(max_length=10, choices=[(s, s) for s in MESSAGE_STATES], default="ok")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["seq"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "seq"], name="unique_seq_per_conversation"
            )
        ]
        indexes = [models.Index(fields=["conversation", "seq"])]

    def __str__(self) -> str:
        return f"{self.role} #{self.seq}"


class GenerationRun(models.Model):
    uuid = models.UUIDField(default=new_uuid, unique=True, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="runs")
    user_message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="runs_as_prompt"
    )
    assistant_message = models.ForeignKey(
        Message, null=True, blank=True, on_delete=models.SET_NULL, related_name="runs_as_answer"
    )
    attempt = models.PositiveIntegerField(default=1)
    idempotency_key = models.CharField(max_length=100)
    content_hash = models.CharField(max_length=64)
    state = models.CharField(
        max_length=20, choices=[(s, s) for s in RUN_STATES], default="preparing"
    )
    snapshot = models.JSONField(default=dict)  # config imutável, SEM segredos
    context_used = models.JSONField(default=list)  # seqs incluídos + omitidos
    requested_model = models.CharField(max_length=200, default="")
    actual_model = models.CharField(max_length=200, null=True, blank=True)
    request_id = models.CharField(max_length=200, null=True, blank=True)
    response_id = models.CharField(max_length=200, null=True, blank=True)
    input_tokens = models.PositiveIntegerField(
        null=True, blank=True
    )  # null = desconhecido, nunca 0 inventado
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    stop_reason = models.CharField(max_length=50, null=True, blank=True)
    truncated = models.BooleanField(default=False)
    error_code = models.CharField(
        max_length=50, null=True, blank=True
    )  # código estável, sanitizado
    error_message = models.CharField(max_length=500, null=True, blank=True)
    worker_pid = models.IntegerField(null=True, blank=True)
    claimed_by = models.CharField(
        max_length=100, default="", blank=True
    )  # M2: dono do claim (compare-and-set); fencing token fica p/ M3
    cancel_requested = models.BooleanField(default=False)
    last_heartbeat = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "idempotency_key"], name="unique_idem_per_conversation"
            )
        ]
        indexes = [
            models.Index(fields=["conversation", "-started_at"]),
            models.Index(fields=["state"]),
        ]

    def __str__(self) -> str:
        return f"run {self.uuid} [{self.state}]"


class ConnectionSettings(models.Model):
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="connection"
    )
    default_model = models.CharField(max_length=200, blank=True, default="")
    base_url = models.CharField(max_length=500, blank=True, default="")
    timeout_seconds = models.PositiveIntegerField(null=True, blank=True)
    max_retries = models.PositiveIntegerField(null=True, blank=True)
    keychain_ref = models.CharField(
        max_length=200, blank=True, default=""
    )  # referência, NUNCA o segredo
    key_origin = models.CharField(max_length=20, blank=True, default="")  # keychain|env|file|none
    revision = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"connection({self.owner})"


class ModelCatalogCache(models.Model):
    profile = models.CharField(max_length=200)  # fingerprint chave + endpoint
    endpoint = models.CharField(max_length=500)
    payload = models.JSONField(default=list)
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["profile"], name="unique_catalog_profile")]

    def __str__(self) -> str:
        return f"catalog({self.endpoint})"
