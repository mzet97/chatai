"""Modelos de ferramentas/MCP/auditoria (T9). Transações curtas; sem select_for_update."""

import uuid

from django.db import models

APPROVAL_DECISIONS = ("pending", "approve", "deny")
INVOCATION_STATES = (
    "requested",
    "awaiting_approval",
    "approved",
    "running",
    "done",
    "failed",
    "refused",
    "unknown",
)
EFFECT_STATES = ("none", "done", "unknown")


def new_public_id() -> uuid.UUID:
    return uuid.uuid4()


class StudyNote(models.Model):
    """Nota auxiliar de estudo (não é instrução de sistema nem memória admin)."""

    uuid = models.UUIDField(default=new_public_id, unique=True, editable=False)
    owner = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="study_notes")
    conversation = models.ForeignKey(
        "chat.Conversation", on_delete=models.CASCADE, related_name="study_notes"
    )
    title = models.CharField(max_length=120)
    text = models.TextField(max_length=4000)
    operation_key = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["owner", "conversation", "-created_at"])]


class ToolApproval(models.Model):
    """Decisão humana vinculada a operação exata (digest de args)."""

    public_id = models.UUIDField(default=new_public_id, unique=True, editable=False)
    owner = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="tool_approvals")
    conversation = models.ForeignKey(
        "chat.Conversation", on_delete=models.CASCADE, related_name="tool_approvals"
    )
    run_uuid = models.CharField(max_length=36)
    tool_use_id = models.CharField(max_length=100)
    anthropic_name = models.CharField(max_length=64)
    tool_version = models.CharField(max_length=40, default="")
    connection_revision = models.PositiveIntegerField(default=0)
    args_digest = models.CharField(max_length=64)
    args_preview = models.CharField(max_length=500, default="")
    decision = models.CharField(
        max_length=10, choices=[(d, d) for d in APPROVAL_DECISIONS], default="pending"
    )
    idempotency_key = models.CharField(max_length=100, null=True, blank=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["public_id"]),
            models.Index(fields=["owner", "conversation"]),
        ]


class ToolInvocation(models.Model):
    """Auditoria de cada chamada: decisão, resultado e efeito conhecido/desconhecido."""

    uuid = models.UUIDField(default=new_public_id, unique=True, editable=False)
    owner = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="tool_invocations"
    )
    conversation = models.ForeignKey(
        "chat.Conversation", on_delete=models.CASCADE, related_name="tool_invocations"
    )
    run_uuid = models.CharField(max_length=36)
    step = models.PositiveIntegerField(default=0)
    tool_use_id = models.CharField(max_length=100)
    anthropic_name = models.CharField(max_length=64)
    args = models.JSONField(default=dict)
    decision = models.CharField(max_length=20, default="requested")
    state = models.CharField(
        max_length=20, choices=[(s, s) for s in INVOCATION_STATES], default="requested"
    )
    effect = models.CharField(
        max_length=10, choices=[(s, s) for s in EFFECT_STATES], default="none"
    )
    result_ok = models.BooleanField(null=True)
    result_text = models.TextField(default="", max_length=40000)
    operation_key = models.CharField(max_length=100, null=True, blank=True, unique=True)
    error_code = models.CharField(max_length=50, null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["step", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "run_uuid", "tool_use_id"],
                name="unique_tool_use_per_run",
            )
        ]
        indexes = [models.Index(fields=["owner", "conversation", "run_uuid"])]


class MCPConnection(models.Model):
    """Cadastro administrativo de servidor MCP (M3/M4 usam; tabela nasce no M2)."""

    uuid = models.UUIDField(default=new_public_id, unique=True, editable=False)
    owner = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="mcp_connections")
    alias = models.CharField(max_length=60)
    transport = models.CharField(
        max_length=20, choices=[("stdio", "stdio"), ("streamable_http", "streamable_http")]
    )
    config = models.JSONField(default=dict)  # sem segredos, sem shell livre
    credential_ref = models.CharField(max_length=200, blank=True, default="")
    state = models.CharField(max_length=20, default="disabled")
    revision = models.PositiveIntegerField(default=1)
    last_protocol_version = models.CharField(max_length=40, blank=True, default="")
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, null=True, blank=True)
    granted_user_ids = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "alias"], name="unique_alias_per_owner")
        ]


class ToolCatalogSnapshot(models.Model):
    """Congela descrição/schema por (conexão, ferramenta) p/ invalidar aprovações."""

    connection = models.ForeignKey(
        MCPConnection, on_delete=models.CASCADE, related_name="snapshots"
    )
    original_name = models.CharField(max_length=200)
    anthropic_name = models.CharField(max_length=64)
    description = models.CharField(max_length=2000, default="")
    input_schema = models.JSONField(default=dict)
    schema_hash = models.CharField(max_length=64)
    revision = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "original_name", "revision"],
                name="unique_snapshot_per_revision",
            )
        ]


class ConversationToolPrefs(models.Model):
    """Seleção de ferramentas por conversa (M5). Default vazio = sem tools."""

    conversation = models.OneToOneField(
        "chat.Conversation", on_delete=models.CASCADE, related_name="tool_prefs"
    )
    enabled = models.JSONField(default=list)  # [stable_id, ...]
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["conversation"])]


class ModelStep(models.Model):
    """Etapa do loop Anthropic dentro de uma GenerationRun (M5 persiste; M2 cria)."""

    run_uuid = models.CharField(max_length=36)
    step = models.PositiveIntegerField()
    model = models.CharField(max_length=200, default="")
    stop_reason = models.CharField(max_length=50, null=True, blank=True)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    tool_use_ids = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["step"]
        constraints = [
            models.UniqueConstraint(fields=["run_uuid", "step"], name="unique_step_per_run")
        ]
