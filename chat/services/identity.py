"""M6: identidade — OIDC Authentik (perfil homelab) e login local (lite).

Vínculo durável usa (issuer, sub); email nunca funde contas (§19). Sem
provider acessível aqui, este módulo entrega a parte verificável
localmente: leitura e validação da configuração (redirect exato, HTTPS
fora localhost, PKCE documentado). O fluxo Authorization Code contra o
Authentik real exige o cluster e está marcado como não verificado em
docs/architecture-v2/verification.md.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

REQUIRED_WHEN_ENABLED = ("OIDC_CLIENT_ID", "OIDC_REDIRECT_URI")


def oidc_config() -> dict:
    """Lê a configuração OIDC do ambiente do processo (ao vivo)."""
    get = os.environ.get
    return {
        "enabled": bool((get("OIDC_ISSUER") or "").strip()),
        "issuer": (get("OIDC_ISSUER") or "").strip(),
        "client_id": (get("OIDC_CLIENT_ID") or "").strip(),
        "redirect_uri": (get("OIDC_REDIRECT_URI") or "").strip(),
        "has_secret": bool(get("OIDC_CLIENT_SECRET")),
    }


def oidc_status() -> dict:
    """Valida a configuração. Nunca devolve segredos."""
    cfg = oidc_config()
    if not cfg["enabled"]:
        return {
            "status": "ok",
            "detail": "OIDC desabilitado: login Django local (perfil local-lite).",
        }
    missing = [k for k in REQUIRED_WHEN_ENABLED if not cfg[k[5:].lower()]]
    if missing:
        return {
            "status": "degraded",
            "detail": f"OIDC incompleto: faltam {', '.join(missing)}.",
        }
    try:
        parsed = urlparse(cfg["redirect_uri"])
    except ValueError:
        return {"status": "degraded", "detail": "OIDC_REDIRECT_URI inválido."}
    if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
        return {
            "status": "degraded",
            "detail": "OIDC_REDIRECT_URI deve ser exato e HTTPS fora localhost.",
        }
    return {
        "status": "unknown",
        "detail": (
            "Configuração completa; provider Authentik não validado aqui "
            "(exige cluster — preflight M6). Bloqueia só o passo OIDC."
        ),
    }
