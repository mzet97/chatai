from django.urls import path

from chat.views import api_conversations, api_messages, api_runs, pages, settings_views

urlpatterns = [
    path("", pages.index, name="index"),
    path("c/<uuid:conv_uuid>/", pages.index, name="conversation"),
    path("settings/", pages.settings_page, name="settings"),
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
    # Envio usa o mesmo path da listagem (dispatcher em api_messages).
    path(
        "api/conversations/<uuid:conv_uuid>/messages/<uuid:msg_uuid>/retry",
        api_messages.retry_message,
        name="api_retry_message",
    ),
    # API runs
    path("api/runs/<uuid:run_uuid>/stream", api_runs.run_stream, name="api_run_stream"),
    path("api/runs/<uuid:run_uuid>/cancel", api_runs.run_cancel, name="api_run_cancel"),
    path("api/runs/<uuid:run_uuid>", api_runs.run_detail, name="api_run_detail"),
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
