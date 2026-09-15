"""M4: Streamable HTTP + SSRF + credenciais + OAuth (T6/T12, sem rede externa)."""

import socket

import pytest

from chat.services.tools import credentials, oauth, ssrf

LOOPBACK_DEMO = "http://127.0.0.1:8139/mcp"


def test_http_loopback_demo_permitido_e_externo_exige_https():
    ssrf.validate_http_target(LOOPBACK_DEMO, allow_loopback_http=True)
    with pytest.raises(ssrf.Rejected):
        ssrf.validate_http_target("http://example.com/mcp", allow_loopback_http=True)
    with pytest.raises(ssrf.Rejected):
        ssrf.validate_http_target(LOOPBACK_DEMO, allow_loopback_http=False)


def test_destinos_indevidos_rejeitados():
    for url in [
        "ftp://127.0.0.1/x",
        "http://127.0.0.1/x",  # sem flag demo (chamado sem allow)
        "https://user:pass@example.com/mcp",  # credencial na URL
        "https:///mcp",
    ]:
        with pytest.raises(ssrf.Rejected):
            ssrf.validate_http_target(url)


def test_ip_privado_e_reservado_rejeitados(monkeypatch):
    real = socket.getaddrinfo

    def fake(host, port, *a, **k):
        if host == "privado.test":
            return [(socket.AF_INET, None, None, None, ("10.1.2.3", port))]
        if host == "v6.test":
            return [(socket.AF_INET6, None, None, None, ("::1", port, 0, 0))]
        return real(host, port, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with pytest.raises(ssrf.Rejected):
        ssrf.validate_http_target("https://privado.test/mcp")
    with pytest.raises(ssrf.Rejected):
        ssrf.validate_http_target("https://v6.test/mcp")


def test_redirect_sem_downgrade_e_mesmo_guard(monkeypatch):
    real = socket.getaddrinfo

    def fake(host, port, *a, **k):
        if host == "a.test":
            return [(socket.AF_INET, None, None, None, ("203.0.113.7", port))]
        return real(host, port, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with pytest.raises(ssrf.Rejected):
        ssrf.validate_redirect("https://a.test/mcp", "http://a.test/outro")
    ok = ssrf.validate_redirect("https://a.test/mcp", "https://a.test/outro")
    assert ok.startswith("https://")


def test_cofre_roundtrip_e_referencia_sem_valor(monkeypatch):
    store = {}
    monkeypatch.setattr(credentials, "_backend_set", lambda s, a, v: store.update({(s, a): v}))
    monkeypatch.setattr(credentials, "_backend_get", lambda s, a: store.get((s, a)))
    monkeypatch.setattr(credentials, "_backend_delete", lambda s, a: store.pop((s, a), None))
    ref = credentials.store(7, "demo", "segredo-abc")
    assert credentials.retrieve(7, "demo") == "segredo-abc"
    assert "segredo-abc" not in ref  # referência não carrega o valor
    credentials.revoke(7, "demo")
    assert credentials.retrieve(7, "demo") is None


def test_oauth_pkce_e_estado_uso_unico():
    verifier, challenge = oauth.new_pkce_pair()
    assert oauth.s256_challenge(verifier) == challenge
    assert len(verifier) >= 43
    st = oauth.new_state()
    assert oauth.consume_state(st) is True  # primeiro uso vale
    assert oauth.consume_state(st) is False  # replay bloqueado


def test_oauth_issuer_e_resource():
    oauth.validate_issuer("https://auth.test/issuer")
    with pytest.raises(oauth.Invalid):
        oauth.validate_issuer("http://auth.test/issuer")
    with pytest.raises(oauth.Invalid):
        oauth.validate_issuer("not-a-url")
    oauth.validate_resource("https://mcp.test/api", "https://mcp.test/api")
    with pytest.raises(oauth.Invalid):
        oauth.validate_resource("https://mcp.test/api", "https://outro.test/api")


def test_credencial_de_outro_perfil_nao_reutilizada(monkeypatch):
    store = {}
    monkeypatch.setattr(credentials, "_backend_set", lambda s, a, v: store.update({(s, a): v}))
    monkeypatch.setattr(credentials, "_backend_get", lambda s, a: store.get((s, a)))
    credentials.store(7, "demo", "token-7")
    assert credentials.retrieve(8, "demo") is None  # usuário 8 não lê a do 7
    assert credentials.retrieve(7, "outro") is None  # ref diferente não lê
