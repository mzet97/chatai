"""Nível de variação: mapeamento exato e compatibilidade em três estados."""

import pytest

from chat.services.variation import (
    DEFAULT_LEVEL,
    TEMP_MAP_VERSION,
    normalize_level,
    resolve_compatibility,
    resolve_temperature,
    temperature_for,
)


def test_mapeamento_exato():
    assert temperature_for("low") == 0.2
    assert temperature_for("medium") == 0.5
    assert temperature_for("high") == 0.8
    assert DEFAULT_LEVEL == "medium"
    assert TEMP_MAP_VERSION == "temp-map-v1"


@pytest.mark.parametrize("bad", ["turbo", "0.5", "999", "", None, "temperature=999"])
def test_niveis_desconhecidos_rejeitados(bad):
    with pytest.raises(ValueError):
        normalize_level(bad)


def test_normalize_tolera_caixa_e_espaco():
    assert normalize_level("  High ") == "high"


def test_suportados():
    for model in (
        "claude-opus-4-6",
        "claude-opus-4-5-20251101",
        "claude-sonnet-4-5-20250929",
        "claude-haiku-4-5-20251001",
    ):
        state, reason = resolve_compatibility(model=model, base_url="https://api.anthropic.com")
        assert (state, reason) == ("supported", "")


def test_nao_suportados():
    for model in ("claude-sonnet-5", "claude-opus-5", "claude-fable-5", "claude-fable-5-1"):
        state, reason = resolve_compatibility(model=model, base_url="https://api.anthropic.com")
        assert state == "unsupported"
        assert reason == "Este ajuste não está disponível no modelo selecionado."


def test_desconhecidos_sem_capacidade_inventada():
    for model in ("claude-opus-4-7", "algum-modelo-futuro", ""):
        state, reason = resolve_compatibility(model=model, base_url="https://api.anthropic.com")
        assert state == "unknown"
        assert reason == "Suporte a este ajuste ainda não confirmado."


def test_endpoint_customizado_e_desconhecido():
    state, _ = resolve_compatibility(model="claude-opus-4-6", base_url="https://proxy.interno/v1")
    assert state == "unknown"


def test_resolve_temperatura_so_quando_suportado():
    value, state, reason = resolve_temperature(
        "high", model="claude-haiku-4-5-20251001", base_url="https://api.anthropic.com"
    )
    assert (value, state, reason) == (0.8, "supported", "")
    value, state, reason = resolve_temperature(
        "high", model="claude-sonnet-5", base_url="https://api.anthropic.com"
    )
    assert value is None and state == "unsupported" and reason
    value, state, reason = resolve_temperature(
        "low", model="modelo-xyz", base_url="https://api.anthropic.com"
    )
    assert value is None and state == "unknown" and reason
