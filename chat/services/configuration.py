"""Resolução de configuração com precedência explícita e sem vazar segredos.

Precedência (não secretas): conversa → preferência da interface (ConnectionSettings)
→ ambiente do processo → .env (raiz do projeto) → padrão interno.
Credencial: Keychain selecionada → ambiente → .env.
"""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent.parent.parent
# CHAT_DOTENV_PATH: override documentado (ex.: teste de navegador hermético).
_file_env = dotenv_values(os.environ.get("CHAT_DOTENV_PATH") or BASE_DIR / ".env")

DEFAULTS = {
    "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
    "ANTHROPIC_MODEL": "claude-sonnet-5",
    "CHAT_MAX_OUTPUT_TOKENS": "1024",
    "CHAT_INPUT_TOKEN_BUDGET": "12000",
    "CHAT_API_TIMEOUT_SECONDS": "120",
    "CHAT_API_MAX_RETRIES": "0",
}

KEYRING_SERVICE = "claude-chat-local"


def file_env(name: str) -> str | None:
    return _file_env.get(name)


def env_or_file(name: str, default: str = "") -> tuple[str, str]:
    """Retorna (valor, origem) onde origem ∈ {env, file, default}."""
    if name in os.environ and os.environ[name] != "":
        return os.environ[name], "env"
    val = _file_env.get(name)
    if val:
        return val, "file"
    return default, "default"


def resolve_option(name: str, conversation_value=None, ui_value=None) -> tuple[str, str]:
    """Precedência total para opções não secretas. Retorna (valor, origem)."""
    if conversation_value not in (None, ""):
        return str(conversation_value), "conversation"
    if ui_value not in (None, ""):
        return str(ui_value), "ui"
    value, origin = env_or_file(name, DEFAULTS.get(name, ""))
    if origin == "default":
        return value, "default"
    return value, origin


def resolve_int(name: str, conversation_value=None, ui_value=None) -> tuple[int, str]:
    raw, origin = resolve_option(name, conversation_value, ui_value)
    return int(raw), origin


# --- credencial ---


def keyring_available() -> bool:
    try:
        import keyring

        backend = keyring.get_keyring()
        name = type(backend).__name__.lower()
        return "fail" not in name and "null" not in name
    except Exception:
        return False


def read_keychain(username: str = "api-key") -> str | None:
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, username)
    except Exception:
        return None


def write_keychain(secret: str, username: str = "api-key") -> None:
    import keyring

    keyring.set_password(KEYRING_SERVICE, username, secret)


def delete_keychain(username: str = "api-key") -> None:
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, username)
    except Exception:
        pass


@dataclass
class Credential:
    secret: str | None
    origin: str  # keychain|env|file|none
    keychain_available: bool


def resolve_credential(use_keychain: bool) -> Credential:
    """Keychain (se selecionada na interface) → ambiente → .env. Sem fallback em texto puro."""
    available = keyring_available()
    if use_keychain and available:
        secret = read_keychain()
        if secret:
            return Credential(secret, "keychain", available)
    if "ANTHROPIC_API_KEY" in os.environ and os.environ["ANTHROPIC_API_KEY"]:
        return Credential(os.environ["ANTHROPIC_API_KEY"], "env", available)
    file_key = _file_env.get("ANTHROPIC_API_KEY")
    if file_key:
        return Credential(file_key, "file", available)
    return Credential(None, "none", available)


def key_fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()[:16]


# --- endpoint ---


def validate_base_url(url: str, trusted_hosts: tuple[str, ...] = ("api.anthropic.com",)) -> str:
    """Valida HTTPS/host/porta contra allowlist do servidor. Levanta ValueError."""
    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        raise ValueError("endpoint deve usar https")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("endpoint sem host válido")
    if host in ("127.0.0.1", "localhost") and parsed.scheme == "https":
        return url  # permitido para testes locais com TLS
    if host not in trusted_hosts:
        raise ValueError(f"host '{host}' fora da allowlist do servidor")
    if parsed.username or parsed.password:
        raise ValueError("endpoint não pode embutir credenciais")
    return url


def describe(value: str | None, *, secret: bool = False) -> str:
    if value is None:
        return "ausente"
    return "•••••• (oculto)" if secret else str(value)
