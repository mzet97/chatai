"""Fronteira ModelProvider (Rev1.1 §4/M1): Anthropic nativo obrigatório.

Pequena de propósito: só existe esta fronteira porque há exatamente dois
consumidores conceituais (runtime local + futuro worker homelab) e uma
necessidade de isolamento (recusar outro provedor sem fallback invisível).
Sem framework multi-provedor: o registro resolve "anthropic" e recusa o
resto com `ProviderUnavailable` (estado explícito, nunca troca silenciosa).
"""

from __future__ import annotations

from chat.services.providers.anthropic_provider import AnthropicProvider
from chat.services.providers.base import ModelProvider, ProviderUnavailable

__all__ = ["AnthropicProvider", "ModelProvider", "ProviderUnavailable", "get_provider"]


def get_provider(name: str | None = None) -> ModelProvider:
    """Provedor configurado. `None` = padrão da aplicação (anthropic)."""
    from django.conf import settings

    want = (name if name is not None else settings.AI_PROVIDER).strip().lower()
    if want == "anthropic":
        return AnthropicProvider()
    raise ProviderUnavailable(
        f"Provedor '{name or settings.AI_PROVIDER}' indisponível: "
        "esta entrega opera apenas com Anthropic (AI_PROVIDER=anthropic)."
    )
