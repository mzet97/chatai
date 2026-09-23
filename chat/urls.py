from django.urls import path

from chat.views import (
    api_agents,
    api_conversations,
    api_images,
    api_messages,
    api_ops,
    api_rag,
    api_runs,
    api_tools,
    pages,
    settings_views,
)

urlpatterns = [
    path("", pages.index, name="index"),
    path("c/<uuid:conv_uuid>/", pages.index, name="conversation"),
    path("settings/", pages.settings_page, name="settings"),
    path("knowledge/", pages.knowledge_page, name="knowledge"),
    path("agents/", pages.agents_page, name="agents"),
    path("api/health", api_ops.health, name="api_health"),
    # API conversas
    path("api/conversations", api_conversations.conversations, name="api_conversations"),
    path(
        "api/conversations/<uuid:conv_uuid>",
        api_conversations.conversation_detail,
        name="api_conversation_detail",
    ),
    path(
        "api/conversations/<uuid:conv_uuid>/messages",
        api_messages.conversation_messages,
        name="api_conversation_messages",
    ),
    path(
        "api/conversations/<uuid:conv_uuid>/export",
        api_conversations.conversation_export,
        name="api_conversation_export",
    ),
    # Anexo visível de imagens (M3): stateless, separado do Documento RAG.
    path("api/images", api_images.upload_image, name="api_images"),
    # API agentes
    path("api/agents", api_agents.agents, name="api_agents"),
    path("api/agents/examples", api_agents.agent_examples, name="api_agents_examples"),
    path("api/agents/<uuid:agent_uuid>", api_agents.agent_detail, name="api_agent_detail"),
    path(
        "api/agents/<uuid:agent_uuid>/duplicate",
        api_agents.agent_duplicate,
        name="api_agent_duplicate",
    ),
    path(
        "api/agents/<uuid:agent_uuid>/drafts",
        api_agents.agent_new_draft,
        name="api_agent_draft",
    ),
    path(
        "api/agent-versions/<uuid:version_uuid>",
        api_agents.version_detail,
        name="api_agent_version",
    ),
    path(
        "api/agent-versions/<uuid:version_uuid>/publish",
        api_agents.version_publish,
        name="api_agent_version_publish",
    ),
    # Envio usa o mesmo path da listagem (dispatcher em api_messages).
    path(
        "api/conversations/<uuid:conv_uuid>/messages/<uuid:msg_uuid>/retry",
        api_messages.retry_message,
        name="api_retry_message",
    ),
    # API runs
    path("api/runs/<uuid:run_uuid>/stream", api_runs.run_stream, name="api_run_stream"),
    path("api/runs/<uuid:run_uuid>/cancel", api_runs.run_cancel, name="api_run_cancel"),
    path("api/runs/<uuid:run_uuid>/resume", api_runs.run_resume, name="api_run_resume"),
    path("api/runs/<uuid:run_uuid>/events", api_runs.run_events, name="api_run_events"),
    path("api/runs/<uuid:run_uuid>", api_runs.run_detail, name="api_run_detail"),
    # API comandos duráveis (M2): criação separada da observação
    path(
        "api/conversations/<uuid:conv_uuid>/runs",
        api_runs.runs_command,
        name="api_runs_command",
    ),
    # API ferramentas por conversa (M5) + aprovações + continuação
    path(
        "api/conversations/<uuid:conv_uuid>/tools",
        api_tools.conversation_tools,
        name="api_conversation_tools",
    ),
    path(
        "api/conversations/<uuid:conv_uuid>/tools/catalog",
        api_tools.conversation_tools_catalog,
        name="api_conversation_tools_catalog",
    ),
    path(
        "api/runs/<uuid:run_uuid>/approvals/<uuid:approval_id>/decide",
        api_tools.approval_decide,
        name="api_approval_decide",
    ),
    path("api/runs/<uuid:run_uuid>/continue", api_tools.run_continue, name="api_run_continue"),
    path("api/rag/bases", api_rag.bases, name="api_rag_bases"),
    path("api/rag/bases/<uuid:base_uuid>/documents", api_rag.base_documents, name="api_rag_docs"),
    path("api/rag/bases/<uuid:base_uuid>/upload", api_rag.upload_document, name="api_rag_upload"),
    path("api/rag/jobs/<uuid:job_uuid>", api_rag.job_status, name="api_rag_job"),
    path("api/rag/jobs/<uuid:job_uuid>/retry", api_rag.job_retry, name="api_rag_job_retry"),
    path("api/rag/worker/status", api_rag.worker_status, name="api_rag_worker"),
    path(
        "api/rag/bases/<uuid:base_uuid>/manage",
        api_rag.base_detail,
        name="api_rag_base",
    ),
    path(
        "api/rag/documents/<uuid:doc_uuid>",
        api_rag.document_detail,
        name="api_rag_doc",
    ),
    path(
        "api/rag/documents/<uuid:doc_uuid>/delete",
        api_rag.document_delete,
        name="api_rag_doc_del",
    ),
    path(
        "api/rag/documents/<uuid:doc_uuid>/download",
        api_rag.document_download,
        name="api_rag_dl",
    ),
    path(
        "api/rag/conversations/<uuid:conv_uuid>/citations",
        api_rag.conversation_citations,
        name="api_rag_cites",
    ),
    path(
        "api/rag/conversations/<uuid:conv_uuid>/sources",
        api_rag.conversation_sources,
        name="api_rag_sources",
    ),
    # API config / diagnóstico / modelos
    path("api/settings", settings_views.settings_api, name="api_settings"),
    path("api/settings/save", settings_views.settings_save, name="api_settings_save"),
    path(
        "api/settings/remove-key",
        settings_views.settings_remove_key,
        name="api_settings_remove_key",
    ),
    path("api/settings/diagnose", settings_views.settings_diagnose, name="api_diagnose"),
    path("api/models", settings_views.models_api, name="api_models"),
]
