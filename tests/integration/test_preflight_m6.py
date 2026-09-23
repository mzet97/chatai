"""M6: preflight somente-leitura + health sem segredos.

Sem cluster: prova os comandos/endpoint locais; deploy real, OIDC contra
Authentik e backends externos ficam registrados como não verificados em
docs/architecture-v2/verification.md. Sem chamadas pagas, sem commit.
"""

import json

import pytest

pytestmark = pytest.mark.django_db(transaction=False)


def test_preflight_saida_estruturada(capsys):
    from django.core.management import call_command

    call_command("preflight", format="json")
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data["checks"], list)
    names = {c["name"] for c in data["checks"]}
    assert {
        "runtime",
        "database",
        "anthropic_key",
        "oidc",
        "storage",
        "migrations",
    } <= names
    for check in data["checks"]:
        assert check["status"] in ("ok", "degraded", "unknown")
    blob = captured.out
    assert "sk-ant" not in blob and "SECRET" not in blob


def test_preflight_oidc_desabilitado_e_valido(monkeypatch):
    import io

    from django.core.management import call_command

    monkeypatch.delenv("OIDC_ISSUER", raising=False)
    buf = io.StringIO()
    call_command("preflight", format="json", stdout=buf)
    data = json.loads(buf.getvalue())
    oidc = next(c for c in data["checks"] if c["name"] == "oidc")
    assert oidc["status"] == "ok"
    assert "desabilitado" in oidc["detail"]


def test_preflight_oidc_incompleto_e_degraded(monkeypatch):
    import io

    from django.core.management import call_command

    monkeypatch.setenv("OIDC_ISSUER", "https://auth.exemplo/oidc")
    monkeypatch.delenv("OIDC_CLIENT_ID", raising=False)
    buf = io.StringIO()
    call_command("preflight", format="json", stdout=buf)
    data = json.loads(buf.getvalue())
    oidc = next(c for c in data["checks"] if c["name"] == "oidc")
    assert oidc["status"] == "degraded"


def test_health_publico_sem_credenciais_e_sem_chamada_paga():
    from django.test import Client

    r = Client().get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert "anthropic_configured" in body and isinstance(body["anthropic_configured"], bool)
    raw = json.dumps(body)
    assert "sk-ant" not in raw
