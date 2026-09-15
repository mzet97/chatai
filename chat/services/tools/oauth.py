"""OAuth MCP via SDK oficial (T12, [R13, R14]).

Cobre: PKCE S256, estado de uso único com expiração, callback conhecido,
validação de issuer/resource, scopes mínimos. Troca/refresh usam destinos
verificados; tokens ficam no backend (cofre). Fluxo ao vivo contra IdP real
exige autorização e não é executado na suíte padrão.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from urllib.parse import urlparse

CALLBACK_PATH = "/settings/mcp/oauth/callback"
STATE_TTL_S = 600


class Invalid(Exception):
    pass


_states: dict[str, float] = {}
_states_lock = threading.Lock()


def new_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    return verifier, s256_challenge(verifier)


def s256_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def new_state() -> str:
    state = secrets.token_urlsafe(24)
    with _states_lock:
        _states[state] = time.monotonic() + STATE_TTL_S
    return state


def consume_state(state: str) -> bool:
    """Uso único: True na primeira vez dentro da validade; replay/expirado False."""
    with _states_lock:
        expiry = _states.pop(state, None)
    return expiry is not None and expiry > time.monotonic()


def validate_issuer(issuer: str) -> str:
    parts = urlparse(issuer)
    if parts.scheme != "https" or not parts.hostname:
        raise Invalid(f"Issuer inválido (exige HTTPS com host): {issuer!r}.")
    return issuer.rstrip("/")


def validate_resource(server_url: str, resource: str | None) -> None:
    """Token emitido para outro recurso não é reutilizado (mesma origem)."""
    if resource is None:
        return
    expected = urlparse(server_url)
    got = urlparse(resource)
    if (got.scheme, got.hostname, got.port or "") != (
        expected.scheme,
        expected.hostname,
        expected.port or "",
    ):
        raise Invalid(f"Resource fora do servidor: {resource!r}.")


def callback_url(base: str) -> str:
    return base.rstrip("/") + CALLBACK_PATH


def build_provider(
    *, server_url: str, redirect_uri: str, scopes: list[str], storage
):
    """Fia o `OAuthClientProvider` do SDK com nossos ganchos de validação."""
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    from chat.services.tools import ssrf as _ssrf

    async def _validate_resource(server: str, resource: str | None) -> None:
        _ssrf.validate_http_target(server)
        validate_resource(server, resource)

    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=OAuthClientMetadata(
            client_name="claude-chat-local",
            redirect_uris=[redirect_uri],  # callback conhecido, sem curinga
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=" ".join(scopes) if scopes else None,
            token_endpoint_auth_method="none",
        ),
        storage=storage,
        redirect_handler=None,
        callback_handler=None,
        validate_resource_url=_validate_resource,
    )
