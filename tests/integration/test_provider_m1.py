"""M1 (Rev1.1): fronteira ModelProvider, Anthropic nativo obrigatório.

Sem chamadas pagas, sem rede: cliente real nunca instanciado aqui
(construção via fake injetável). Sem commit.
Cobre: registro resolve "anthropic" e recusa outro provedor; capacidades
por ID exato (desconhecido nunca presumido); normalização de usage com
ausente=desconhecido; defaults AI_PROVIDER/OPENAI_ENABLED; transversal
"sem OpenAI" (suite não importa `openai`, nada exige OPENAI_API_KEY).
"""

import sys

import pytest
from django.conf import settings as dj_settings

pytestmark = pytest.mark.django_db(transaction=True)


def test_registry_defaults_to_anthropic():
    from chat.services.providers import AnthropicProvider, get_provider

    assert isinstance(get_provider(), AnthropicProvider)
    assert isinstance(get_provider("anthropic"), AnthropicProvider)


def test_registry_refuses_other_providers():
    from chat.services.providers import ProviderUnavailable, get_provider

    for name in ("openai", "OPENAI", "anthropic-compatible", "litellm", ""):
        with pytest.raises(ProviderUnavailable):
            get_provider(name)


def test_unknown_model_capabilities_are_unknown():
    from chat.services.providers import get_provider

    caps = get_provider().capabilities(
        model="modelo-que-nao-existe-0000", base_url="https://api.anthropic.com"
    )
    assert caps["thinking"] == "unknown"
    assert caps["vision"] == "unknown"
    assert caps["provider"] == "anthropic"


def test_injected_tables_yield_supported_states():
    from chat.services.providers import get_provider

    caps = get_provider().capabilities(
        model="qualquer-id",
        base_url="https://api.anthropic.com",
        adaptive={"qualquer-id"},
        vision_yes={"qualquer-id"},
    )
    assert caps["thinking"] == "adaptive"
    assert caps["vision"] == "yes"


def test_custom_endpoint_is_unknown_without_evidence():
    from chat.services.providers import get_provider

    caps = get_provider().capabilities(
        model="qualquer-id", base_url="https://proxy.interno.exemplo/v1"
    )
    assert caps["thinking"] == "unknown"
    assert caps["vision"] == "unknown"


def test_normalize_usage_missing_is_unknown():
    from chat.services.providers import get_provider

    parsed = get_provider().normalize_usage(None)
    assert parsed["input_tokens"] is None
    assert parsed["cache_creation_input_tokens"] is None
    assert parsed["cache_read_input_tokens"] is None


def test_settings_provider_defaults():
    assert dj_settings.AI_PROVIDER == "anthropic"
    assert dj_settings.OPENAI_ENABLED is False


def test_no_openai_dependency_at_runtime(monkeypatch):
    import os
    import re

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert "OPENAI_API_KEY" not in os.environ
    import chat.services.generation  # noqa: F401
    import chat.services.providers  # noqa: F401

    assert "openai" not in sys.modules
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    for pkg in ("chat", "config"):
        for dirpath, _dirnames, filenames in os.walk(os.path.join(root, pkg)):
            if "__pycache__" in dirpath:
                continue
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                src = open(os.path.join(dirpath, fn), encoding="utf-8").read()
                if re.search(r"(?m)^\s*(import|from)\s+openai\b", src):
                    offenders.append(os.path.join(dirpath, fn))
    assert offenders == []
