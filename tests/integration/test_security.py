"""Critério 16 (RNF-01): login, CSRF, XSS/armazenamento, endpoint não autorizado."""

import pytest

pytestmark = pytest.mark.django_db


def test_mutations_require_login(client):
    assert client.post("/api/conversations").status_code in (301, 302)
    assert client.get("/api/conversations").status_code in (301, 302)


def test_csrf_enforced_on_post(logged_client, conversation):
    logged_client.handler.enforce_csrf_checks = True
    try:
        r = logged_client.post(
            f"/api/conversations/{conversation.uuid}/messages",
            data={"content": "oi", "idempotency_key": "k"},
            content_type="application/json",
        )
        assert r.status_code == 403  # sem token CSRF → bloqueado
    finally:
        logged_client.handler.enforce_csrf_checks = False


def test_api_returns_raw_text_and_templates_escape(logged_client, conversation):
    """XSS: a API devolve o texto cru via JSON (sem HTML ativo); o template escapa."""
    from chat.models import Message

    Message.objects.create(
        conversation=conversation, seq=1, role="assistant", text='<script>alert("xss")</script>'
    )
    r = logged_client.get(f"/api/conversations/{conversation.uuid}/messages")
    assert r.json()["results"][0]["text"] == '<script>alert("xss")</script>'
    # Página inicial escapa (Django autoescape) — nenhum <script> cru do dado.
    html = logged_client.get("/").content.decode()
    assert "<script>alert" not in html
    # JS nunca usa innerHTML sobre deltas: texto puro via textContent + DOM seguro.
    from pathlib import Path

    js = Path("chat/static/chat/js/chat.js").read_text()
    assert "textContent = acc" in js
    md = Path("chat/static/chat/js/markdown.js").read_text()
    code = "\n".join(line for line in md.splitlines() if not line.strip().startswith("//"))
    assert "innerHTML" not in code  # só DOM seguro: createElement/textContent


def test_key_never_leaves_backend(logged_client, monkeypatch):
    """Nenhuma resposta JSON/HTML contém o segredo — só a origem."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-secret-123")
    data = logged_client.get("/api/settings").json()
    blob = str(data)
    assert "sk-ant-fake-secret-123" not in blob
    assert data["key_origin"] in ("env", "file")
    for url in ("/settings/", "/"):
        assert "sk-ant-fake-secret-123" not in logged_client.get(url).content.decode()


def test_untrusted_endpoint_rejected(logged_client):
    r = logged_client.post(
        "/api/settings/save",
        data={"base_url": "https://evil.example.com", "confirm_endpoint_change": True},
        content_type="application/json",
    )
    assert r.status_code == 400
