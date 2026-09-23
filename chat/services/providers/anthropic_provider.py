"""AnthropicProvider: único provedor operacional (Rev1.1 §11).

Delega para os serviços existentes (`anthropic_client`, `thinking`,
`agents.cache`) — sem duplicar lógica, sem segundo executor. Tabelas de
capacidade por ID exato; fora da tabela ou endpoint customizado =
"unknown" (nunca inferido pelo nome). `**tables` permite injeção em teste
no padrão já usado por `thinking.capability_for`/`vision_for`.
"""

from __future__ import annotations

from typing import Any

from chat.services import thinking as _thinking
from chat.services.agents import cache as _cache
from chat.services.anthropic_client import build_client as _build
from chat.services.anthropic_client import classify_error as _classify
from chat.services.providers.base import ModelProvider


class AnthropicProvider(ModelProvider):
    name = "anthropic"

    def build_client(self, **kwargs) -> Any:
        return _build(**kwargs)

    def capabilities(self, *, model: str, base_url: str, **tables) -> dict:
        think_kwargs = {
            k: tables[k] for k in ("adaptive", "legacy", "always_on", "none") if k in tables
        }
        vision_kwargs = {k: tables[k] for k in ("vision_yes", "vision_no") if k in tables}
        if "vision_yes" in vision_kwargs:
            vision_kwargs["yes"] = vision_kwargs.pop("vision_yes")
        if "vision_no" in vision_kwargs:
            vision_kwargs["no"] = vision_kwargs.pop("vision_no")
        return {
            "provider": self.name,
            "thinking": _thinking.capability_for(model=model, base_url=base_url, **think_kwargs),
            "vision": _thinking.vision_for(model=model, base_url=base_url, **vision_kwargs),
        }

    def normalize_usage(self, usage) -> dict:
        return _cache.parse_usage(usage)

    def classify_error(self, exc: BaseException) -> tuple[str, str]:
        return _classify(exc)
