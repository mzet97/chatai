"""Exportação sem segredos (RF-02). Exportações contêm conteúdo privado (aviso na UI)."""

EXPORT_VERSION = "claude-chat-local/export-v1"


def export_json(conversation, messages, runs) -> dict:
    return {
        "format": EXPORT_VERSION,
        "privacy_notice": "Este arquivo contém o conteúdo privado da conversa. Guarde com cuidado.",
        "conversation": {
            "uuid": str(conversation.uuid),
            "title": conversation.title,
            "archived": conversation.archived,
            "preferred_model": conversation.preferred_model,
            "system_prompt": conversation.system_prompt,
            "created_at": conversation.created_at.isoformat(),
            "updated_at": conversation.updated_at.isoformat(),
        },
        "messages": [
            {
                "seq": m.seq,
                "role": m.role,
                "text": m.text,
                "state": m.state,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
        "runs": [
            {
                "attempt": r.attempt,
                "state": r.state,
                "requested_model": r.requested_model,
                "actual_model": r.actual_model,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "stop_reason": r.stop_reason,
                "truncated": r.truncated,
                "started_at": r.started_at.isoformat(),
            }
            for r in runs
        ],
    }


def export_markdown(conversation, messages) -> str:
    lines = [
        f"# {conversation.title}",
        "",
        "> Exportado do claude-chat-local. Conteúdo privado — guarde com cuidado.",
        "",
    ]
    if conversation.system_prompt:
        lines += ["## Instruções da conversa", "", conversation.system_prompt, ""]
    lines += ["## Conversa", ""]
    for m in messages:
        who = "Você" if m.role == "user" else "Assistente"
        extra = "" if m.state == "ok" else f" *({m.state})*"
        lines += [f"### {who}{extra} — {m.created_at:%d/%m/%Y %H:%M}", "", m.text, ""]
    return "\n".join(lines)
