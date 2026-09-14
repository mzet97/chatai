"""Helpers: propriedade por owner em todas as consultas (RNF-01)."""

from django.shortcuts import get_object_or_404

from chat.models import Conversation, GenerationRun


def owned_conversation(user, uuid):
    return get_object_or_404(Conversation, uuid=uuid, owner=user)


def owned_run(user, uuid):
    return get_object_or_404(GenerationRun, uuid=uuid, conversation__owner=user)
