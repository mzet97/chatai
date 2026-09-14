"""Critério 17 (exportação sem segredos)."""

from chat.services import exports


def test_export_json_has_no_secrets(conversation, user):
    from chat.models import GenerationRun, Message

    m = Message.objects.create(conversation=conversation, seq=1, role="user", text="oi")
    run = GenerationRun.objects.create(
        conversation=conversation,
        user_message=m,
        idempotency_key="k",
        content_hash="h",
        snapshot={"api_key": "NUNCA", "model": "x"},
    )
    payload = exports.export_json(conversation, [m], [run])
    blob = str(payload)
    assert "NUNCA" not in blob and "sk-" not in blob
    assert payload["format"].startswith("claude-chat-local/export-v")
    assert "privacy" in payload["privacy_notice"].lower() or "privado" in payload["privacy_notice"]


def test_export_markdown_contains_history(conversation):
    from chat.models import Message

    Message.objects.create(conversation=conversation, seq=1, role="user", text="pergunta?")
    md = exports.export_markdown(conversation, list(conversation.messages.all()))
    assert "pergunta?" in md and "privado" in md.lower()
