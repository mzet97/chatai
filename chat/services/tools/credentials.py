"""Cofre de credenciais MCP (T12): valores no Keychain, SQLite guarda referências.

Conta = "<user_id>/<ref>". Nunca loga valores; `store` devolve a referência
(sem o segredo) para persistir na MCPConnection.
"""

from __future__ import annotations

SERVICE = "claude-chat-local/mcp"


def _backend_set(service: str, account: str, value: str) -> None:
    import keyring

    keyring.set_password(service, account, value)


def _backend_get(service: str, account: str) -> str | None:
    import keyring

    return keyring.get_password(service, account)


def _backend_delete(service: str, account: str) -> None:
    import keyring

    try:
        keyring.delete_password(service, account)
    except keyring.errors.PasswordDeleteError:
        pass


def _account(user_id: int, ref: str) -> str:
    return f"{int(user_id)}/{ref}"


def store(user_id: int, ref: str, value: str) -> str:
    """Guarda e devolve a referência persistível (sem o valor)."""
    if not ref or not value:
        raise ValueError("Referência e valor são obrigatórios.")
    _backend_set(SERVICE, _account(user_id, ref), value)
    return f"keychain:{SERVICE}:{_account(user_id, ref)}"


def retrieve(user_id: int, ref: str) -> str | None:
    return _backend_get(SERVICE, _account(user_id, ref))


def revoke(user_id: int, ref: str) -> None:
    _backend_delete(SERVICE, _account(user_id, ref))
