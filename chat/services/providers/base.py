"""Contrato mínimo de provedor de geração (Rev1.1 §4/M1)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ProviderUnavailable(Exception):
    """Provedor solicitado não existe nesta entrega (sem fallback invisível)."""


class ModelProvider(ABC):
    """Superfície usada pelo runtime: cliente, capacidades, usage, erros."""

    name: str = "unknown"

    @abstractmethod
    def build_client(self, **kwargs) -> Any:
        """Constrói o cliente nativo do SDK (síncrono; sem I/O)."""

    @abstractmethod
    def capabilities(self, *, model: str, base_url: str, **tables) -> dict:
        """`{provider, thinking, vision}` por ID exato; desconhecido=unknown."""

    @abstractmethod
    def normalize_usage(self, usage) -> dict:
        """Usage nativo → métricas; ausente = desconhecido (None), nunca zero."""

    @abstractmethod
    def classify_error(self, exc: BaseException) -> tuple[str, str]:
        """Exceção do SDK → (código estável, mensagem sem segredo)."""
