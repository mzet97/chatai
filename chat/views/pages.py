"""Páginas server-rendered (shell do chat, configurações, login usa auth.views)."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from chat.services import configuration as cfg
from chat.services.anthropic_client import resolve_for_user
from chat.views._scoping import owned_conversation


@login_required
def index(request, conv_uuid=None):
    conversation = None
    if conv_uuid:
        conversation = owned_conversation(request.user, conv_uuid)
    conversations = request.user.conversations.filter(archived=False).order_by("-updated_at")[:50]
    resolved, _cred = resolve_for_user(request.user, conversation)
    return render(
        request,
        "chat/index.html",
        {
            "conversation": conversation,
            "conversations": conversations,
            "effective": resolved.as_dict(),
            "key_configured": _cred.origin != "none",
        },
    )


@login_required
@login_required
def knowledge_page(request):
    """Área Conhecimento (M5): bases, upload, estados, exclusão."""
    return render(request, "chat/knowledge.html", {})


@login_required
def agents_page(request):
    """Página Agentes (M1/AG-6): cadastro, versões, publicar, arquivar, exemplos."""
    return render(request, "chat/agents.html", {})


@login_required
def settings_page(request):
    from chat.models import ConnectionSettings

    ui = ConnectionSettings.objects.filter(owner=request.user).first()
    resolved, cred = resolve_for_user(request.user)
    return render(
        request,
        "chat/settings.html",
        {
            "ui": ui,
            "effective": resolved.as_dict(),
            "key_origin": cred.origin,
            "keychain_available": cfg.keyring_available(),
        },
    )
