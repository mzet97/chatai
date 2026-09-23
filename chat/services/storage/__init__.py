"""ObjectStore (M3): fronteira de binários privados com backend local.

LocalObjectStore é a implementação local-lite (diretório privado). O
backend S3 do perfil homelab implementa esta mesma ABC (ver migration.md);
nenhum chamador conhece o backend. Objetos imutáveis com SHA-256 próprio;
staging com commit atômico; sem transação única com o banco.
"""

from __future__ import annotations

import hashlib
import secrets
import shutil
import time
from abc import ABC, abstractmethod
from pathlib import Path

STAGED_PREFIX = ".staging-"


class ObjectStore(ABC):
    """Contrato mínimo: put/get/exists/delete + staging + limpeza."""

    @abstractmethod
    def put(self, key: str, content: bytes) -> dict:
        """Grava bytes na chave; retorna {sha256, size}."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Lê bytes; FileNotFoundError quando ausente."""

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Remove; True quando existia."""

    @abstractmethod
    def stage(self, content: bytes) -> str:
        """Grava rascunho e retorna token de uso único."""

    @abstractmethod
    def commit(self, token: str, key: str) -> dict:
        """Publica o rascunho na chave (atômico); token de uso único."""

    @abstractmethod
    def abort(self, token: str) -> None:
        """Descarta o rascunho."""

    @abstractmethod
    def sweep_staged(self, max_age_s: int = 3600) -> int:
        """Remove rascunhos mais velhos; retorna quantidade removida."""


class LocalObjectStore(ObjectStore):
    """Backend local-lite: diretório privado, sem dependência nova."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._staged = self.root / ".staging"
        self._staged.mkdir(parents=True, exist_ok=True)
        self._live_tokens: set[str] = set()

    def _resolve(self, key: str) -> Path:
        if not key or not isinstance(key, str):
            raise ValueError("Chave vazia.")
        pure = Path(key)
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError(f"Chave inválida: {key!r}")
        dest = (self.root / pure).resolve()
        root = self.root.resolve()
        if dest != root and root not in dest.parents:
            raise ValueError(f"Chave fora da raiz: {key!r}")
        return dest

    @staticmethod
    def _meta(content: bytes) -> dict:
        return {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}

    def put(self, key: str, content: bytes) -> dict:
        dest = self._resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        return self._meta(content)

    def get(self, key: str) -> bytes:
        dest = self._resolve(key)
        if not dest.is_file():
            raise FileNotFoundError(f"Objeto ausente: {key!r}")
        return dest.read_bytes()

    def exists(self, key: str) -> bool:
        try:
            return self._resolve(key).is_file()
        except ValueError:
            return False

    def delete(self, key: str) -> bool:
        dest = self._resolve(key)
        if not dest.is_file():
            return False
        dest.unlink()
        return True

    def _staged_path(self, token: str) -> Path:
        if not token or "/" in token or token.startswith("."):
            raise ValueError("Token inválido.")
        path = self._staged / (STAGED_PREFIX + token)
        if not path.is_file() or token not in self._live_tokens:
            raise ValueError("Rascunho inexistente ou já consumido.")
        return path

    def stage(self, content: bytes) -> str:
        token = secrets.token_hex(16)
        (self._staged / (STAGED_PREFIX + token)).write_bytes(content)
        self._live_tokens.add(token)
        return token

    def commit(self, token: str, key: str) -> dict:
        staged = self._staged_path(token)
        content = staged.read_bytes()
        meta = self.put(key, content)
        staged.unlink()
        self._live_tokens.discard(token)
        return meta

    def abort(self, token: str) -> None:
        try:
            staged = self._staged_path(token)
        except ValueError:
            return
        staged.unlink(missing_ok=True)
        self._live_tokens.discard(token)

    def sweep_staged(self, max_age_s: int = 3600) -> int:
        cutoff = time.time() - max_age_s
        removed = 0
        for path in self._staged.glob(STAGED_PREFIX + "*"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    self._live_tokens.discard(path.name[len(STAGED_PREFIX) :])
                    removed += 1
            except OSError:
                continue
        return removed


def remove_tree(path: Path) -> None:
    """Remoção de árvore (exclusão lógica→física de versões)."""
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
