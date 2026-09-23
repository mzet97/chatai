"""Modelos de agentes (AG-1/AG-3/AG-5): perfis versionados, árvore de execução,
orçamento, cache observado e trilha de eventos. Sem banco paralelo; apenas
a raiz ocupa a exclusividade da conversa."""

import uuid

from django.conf import settings
from django.db import models

AGENT_MODES = ("chat", "agent", "team")

CACHE_MODES = ("disabled", "stable", "conversation")
CACHE_TTLS = ("5m", "1h")

RUN_STATES = (
    "created",
    "running",
    "awaiting_approval",
    "paused",
    "done",
    "failed",
    "cancelled",
    "budget_exhausted",
)


def new_public_id() -> uuid.UUID:
    return uuid.uuid4()


class AgentDefinition(models.Model):
    """Perfil de agente editável; versões publicadas vivem em AgentVersion."""

    uuid = models.UUIDField(default=new_public_id, unique=True, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    description = models.TextField(default="", blank=True)
    kind = models.CharField(
        max_length=20,
        default="general",
        help_text="general | researcher | reviewer | coordinator (rótulo inicial).",
    )
    archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="unique_agent_name_per_owner")
        ]

    def __str__(self):
        return self.name


class AgentVersion(models.Model):
    """Versão publicada: imutável após publish (AG-1)."""

    uuid = models.UUIDField(default=new_public_id, unique=True, editable=False)
    definition = models.ForeignKey(
        AgentDefinition, on_delete=models.CASCADE, related_name="versions"
    )
    revision = models.PositiveIntegerField()
    task_instructions = models.TextField(default="", blank=True)
    model = models.CharField(max_length=200, default="", blank=True)
    allowed_tools = models.JSONField(default=list)
    allowed_kb_ids = models.JSONField(default=list)
    delegatable_ids = models.JSONField(default=list)
    thinking_mode = models.CharField(max_length=10, default="default")
    thinking_level = models.CharField(max_length=10, default="medium")
    thinking_budget = models.PositiveIntegerField(default=1024)
    variation_level = models.CharField(max_length=10, default="medium")
    cache_mode = models.CharField(max_length=20, default="stable")
    cache_ttl = models.CharField(max_length=5, default="5m")
    max_child_runs = models.PositiveIntegerField(default=0)
    published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-revision"]
        constraints = [
            models.UniqueConstraint(fields=["definition", "revision"], name="unique_agent_revision")
        ]

    def __str__(self):
        return f"{self.definition.name} r{self.revision}"


class AgentRun(models.Model):
    """Execução (raiz ou filha): árvore, tarefa, snapshot e checkpoints."""

    uuid = models.UUIDField(default=new_public_id, unique=True, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    conversation = models.ForeignKey(
        "chat.Conversation", on_delete=models.CASCADE, related_name="agent_runs"
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )
    agent_version = models.ForeignKey(
        AgentVersion, null=True, blank=True, on_delete=models.SET_NULL
    )
    depth = models.PositiveIntegerField(default=0)
    task = models.TextField(default="", blank=True)
    completion_criteria = models.TextField(default="", blank=True)
    state = models.CharField(max_length=20, default="created")
    result_summary = models.TextField(default="", blank=True)
    result_evidence = models.JSONField(default=list)
    limitations = models.TextField(default="", blank=True)
    failure_reason = models.TextField(default="", blank=True)
    snapshot = models.JSONField(default=dict)
    checkpoint = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"run {self.uuid} ({self.state})"


class Delegation(models.Model):
    """Vínculo tool_use do pai → AgentRun filho + contexto liberado."""

    uuid = models.UUIDField(default=new_public_id, unique=True, editable=False)
    parent_run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="delegations")
    child_run = models.OneToOneField(AgentRun, on_delete=models.CASCADE, related_name="delegation")
    tool_use_id = models.CharField(max_length=200, default="")
    released_context = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"delegation {self.uuid}"


class BudgetLedger(models.Model):
    """Uma linha por reserva/consumo; agregação por run raiz, sem dupla contagem."""

    uuid = models.UUIDField(default=new_public_id, unique=True, editable=False)
    root_run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="ledger_lines")
    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="own_ledger_lines")
    kind = models.CharField(
        max_length=20,
        help_text="reserve_output | usage | cache_write | cache_read | child_slot | call_slot",
    )
    tokens = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=300, default="", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class CacheObservation(models.Model):
    """Plano aplicado + métricas de cache por chamada (AG-5)."""

    uuid = models.UUIDField(default=new_public_id, unique=True, editable=False)
    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="cache_observations")
    step = models.PositiveIntegerField(default=0)
    mode = models.CharField(max_length=20, default="disabled")
    ttl = models.CharField(max_length=5, default="5m")
    eligible = models.BooleanField(default=False)
    diagnosis = models.CharField(max_length=300, default="", blank=True)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    cache_creation_input_tokens = models.PositiveIntegerField(null=True, blank=True)
    cache_read_input_tokens = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["step"]


class RunEvent(models.Model):
    """Trilha sequenciada para UI e recuperação (AG-3/§16)."""

    uuid = models.UUIDField(default=new_public_id, unique=True, editable=False)
    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="events")
    seq = models.PositiveIntegerField()
    kind = models.CharField(max_length=40)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["seq"]
        constraints = [
            models.UniqueConstraint(fields=["run", "seq"], name="unique_event_seq_per_run")
        ]
