"""Django Admin: MCP + RAG + ferramentas (staff local).

MCPConnection é o cadastro administrativo de servidores MCP: alias,
transporte (stdio/streamable_http), config sem segredos e estado.
Segredos ficam no Keychain; aqui só aparece `credential_ref`.
"""

from django.contrib import admin

from chat.models import (
    ConnectionSettings,
    Conversation,
    GenerationRun,
    Message,
    ModelCatalogCache,
)
from chat.models_rag import (
    Chunk,
    ChunkEmbedding,
    Citation,
    ConversationKnowledge,
    Document,
    DocumentVersion,
    EmbeddingProfile,
    Evidence,
    IngestionJob,
    KnowledgeBase,
    RetrievalRun,
    SourceDependency,
)
from chat.models_tools import (
    ConversationToolPrefs,
    MCPConnection,
    ModelStep,
    StudyNote,
    ToolApproval,
    ToolCatalogSnapshot,
    ToolInvocation,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    """Auditoria: ver, sem adicionar/alterar/excluir."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method == "GET"

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MCPConnection)
class MCPConnectionAdmin(admin.ModelAdmin):
    list_display = ("alias", "owner", "transport", "state", "revision", "created_at")
    list_filter = ("transport", "state")
    search_fields = ("alias", "owner__username")
    actions = ("rediscover_tools",)

    @admin.action(description="Redescobrir ferramentas (atualiza snapshots)")
    def rediscover_tools(self, request, queryset):
        from chat.services.tools import discovery

        failed = []
        for conn in queryset:
            try:
                n = discovery.refresh_connection(conn.pk)["tools"]
                self.message_user(request, f"{conn.alias}: {n} ferramenta(s).")
            except Exception as exc:  # noqa: BLE001 — erro por conexão, sem travar o lote
                failed.append(f"{conn.alias}: {exc}")
        for msg in failed:
            self.message_user(request, msg, level="error")
        if not queryset.exists():
            self.message_user(request, "Nada selecionado.", level="warning")

    readonly_fields = (
        "uuid",
        "revision",
        "last_protocol_version",
        "last_checked_at",
        "created_at",
    )
    fields = (
        "alias",
        "owner",
        "transport",
        "state",
        "config",
        "credential_ref",
        "granted_user_ids",
        "uuid",
        "revision",
        "last_protocol_version",
        "last_checked_at",
        "last_error",
        "created_at",
    )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # JSONFields com default continuam required no form; vazio = default.
        form.base_fields["config"].required = False
        form.base_fields["granted_user_ids"].required = False
        form.base_fields["credential_ref"].help_text = (
            "Referência do Keychain (nunca o segredo). Vazio = servidor sem autenticação."
        )
        form.base_fields["config"].help_text = (
            "Sem segredos e sem shell livre. stdio exige caminho absoluto; "
            "streamable_http exige URL https/http + allowlist."
        )
        return form


@admin.register(KnowledgeBase)
class KnowledgeBaseAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "active_revision", "created_at")
    search_fields = ("name", "owner__username")
    readonly_fields = ("uuid", "created_at")


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("name", "base", "owner", "state", "created_at")
    list_filter = ("state",)
    search_fields = ("name", "owner__username")
    readonly_fields = ("uuid", "created_at")


@admin.register(DocumentVersion)
class DocumentVersionAdmin(admin.ModelAdmin):
    list_display = (
        "document",
        "number",
        "filename",
        "size_bytes",
        "extractor",
        "is_staging",
        "created_at",
    )
    list_filter = ("is_staging", "extractor")
    readonly_fields = ("uuid", "sha256", "rel_path", "text", "created_at")


@admin.register(IngestionJob)
class IngestionJobAdmin(admin.ModelAdmin):
    list_display = ("document", "state", "attempt", "generation", "updated_at")
    list_filter = ("state",)
    readonly_fields = (
        "uuid",
        "owner",
        "document",
        "version",
        "attempt",
        "generation",
        "claimed_by",
        "lease_until",
        "checkpoint",
        "error",
        "client_key",
        "progress_known",
        "progress_total",
        "created_at",
        "updated_at",
    )


@admin.register(EmbeddingProfile)
class EmbeddingProfileAdmin(admin.ModelAdmin):
    list_display = ("model_id", "revision", "dim", "pipeline_version")
    readonly_fields = ("created_at",)


@admin.register(Chunk)
class ChunkAdmin(ReadOnlyAdmin):
    list_display = ("version", "order", "token_count", "created_at")
    list_filter = ("profile",)


@admin.register(ChunkEmbedding)
class ChunkEmbeddingAdmin(ReadOnlyAdmin):
    list_display = ("id", "chunk", "profile")


@admin.register(RetrievalRun)
class RetrievalRunAdmin(ReadOnlyAdmin):
    list_display = ("id", "owner", "created_at")


@admin.register(Evidence)
class EvidenceAdmin(ReadOnlyAdmin):
    list_display = ("id", "run", "chunk", "order")


@admin.register(ConversationKnowledge)
class ConversationKnowledgeAdmin(admin.ModelAdmin):
    list_display = ("conversation", "mode", "updated_at")
    readonly_fields = ("updated_at",)


@admin.register(Citation)
class CitationAdmin(ReadOnlyAdmin):
    list_display = ("id", "run", "source_index", "created_at")


@admin.register(SourceDependency)
class SourceDependencyAdmin(ReadOnlyAdmin):
    list_display = ("id", "citation", "base_uuid", "version_number")


@admin.register(ConversationToolPrefs)
class ConversationToolPrefsAdmin(admin.ModelAdmin):
    list_display = ("conversation", "updated_at")
    readonly_fields = ("updated_at",)


@admin.register(ToolApproval)
class ToolApprovalAdmin(ReadOnlyAdmin):
    list_display = ("anthropic_name", "owner", "decision", "created_at")
    list_filter = ("decision",)


@admin.register(ToolInvocation)
class ToolInvocationAdmin(ReadOnlyAdmin):
    list_display = ("anthropic_name", "owner", "decision", "state", "effect", "started_at")
    list_filter = ("decision", "state", "effect")


@admin.register(ToolCatalogSnapshot)
class ToolCatalogSnapshotAdmin(ReadOnlyAdmin):
    list_display = ("connection", "original_name")


@admin.register(ModelStep)
class ModelStepAdmin(ReadOnlyAdmin):
    list_display = ("run_uuid", "step", "model", "stop_reason", "created_at")


@admin.register(StudyNote)
class StudyNoteAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "conversation", "created_at")
    search_fields = ("title", "owner__username")
    readonly_fields = ("uuid", "created_at")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "archived", "updated_at")
    list_filter = ("archived",)
    search_fields = ("title", "owner__username")
    readonly_fields = ("uuid", "created_at", "updated_at")


@admin.register(Message)
class MessageAdmin(ReadOnlyAdmin):
    list_display = ("id", "conversation", "role", "created_at")
    list_filter = ("role",)


@admin.register(GenerationRun)
class GenerationRunAdmin(ReadOnlyAdmin):
    list_display = ("conversation", "state", "requested_model", "started_at")
    list_filter = ("state",)


@admin.register(ConnectionSettings)
class ConnectionSettingsAdmin(admin.ModelAdmin):
    list_display = ("owner", "default_model", "key_origin", "revision", "updated_at")
    readonly_fields = ("keychain_ref", "revision", "updated_at")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields[
            "keychain_ref"
        ].help_text = "Referência do Keychain — nunca o segredo, nunca editável aqui."
        return form


@admin.register(ModelCatalogCache)
class ModelCatalogCacheAdmin(ReadOnlyAdmin):
    list_display = ("profile", "endpoint", "fetched_at")
