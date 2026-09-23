"""M1: capacidades de pensamento — zero payload incompatível."""

import pytest

from chat.services import thinking as T

ADAPTIVE = {"adaptive": {"m-adapt"}}
LEGACY = {"legacy": {"m-legacy"}}
ALWAYS = {"always_on": {"m-always"}}
NONE = {"none": {"m-none"}}


def test_default_nunca_envia_chaves():
    r = T.resolve_thinking(
        "default",
        "high",
        model="m-adapt",
        base_url="https://api.anthropic.com",
        max_tokens=4096,
        show_summary=True,
        **ADAPTIVE,
    )
    assert r["thinking"] is None and r["output_config"] is None
    assert r["state"] == "default" and r["conflict"] is None


def test_desconhecido_omite_tudo():
    r = T.resolve_thinking(
        "enabled", "medium", model="m-???", base_url="https://api.anthropic.com", max_tokens=4096
    )
    assert r["thinking"] is None and r["output_config"] is None
    assert r["state"] == "unknown" and r["reason"]
    r2 = T.resolve_thinking(
        "enabled",
        "medium",
        model="m-adapt",
        base_url="https://custom.local/v1",
        max_tokens=4096,
        **ADAPTIVE,
    )
    assert r2["state"] == "unknown" and r2["thinking"] is None


def test_adaptativo_mapeia_effort_e_display():
    r = T.resolve_thinking(
        "enabled",
        "high",
        model="m-adapt",
        base_url="https://api.anthropic.com",
        max_tokens=4096,
        show_summary=True,
        **ADAPTIVE,
    )
    assert r["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert r["output_config"] == {"effort": "high"}
    r2 = T.resolve_thinking(
        "enabled",
        "low",
        model="m-adapt",
        base_url="https://api.anthropic.com",
        max_tokens=4096,
        show_summary=False,
        **ADAPTIVE,
    )
    assert r2["output_config"] == {"effort": "low"}
    assert r2["thinking"]["display"] == "omitted"


def test_legado_orcamento_e_conflito():
    ok = T.resolve_thinking(
        "enabled",
        "medium",
        model="m-legacy",
        base_url="https://api.anthropic.com",
        max_tokens=4096,
        budget=2048,
        show_summary=True,
        **LEGACY,
    )
    assert ok["thinking"] == {"type": "enabled", "budget_tokens": 2048, "display": "summarized"}
    assert ok["conflict"] is None
    bad = T.resolve_thinking(
        "enabled",
        "medium",
        model="m-legacy",
        base_url="https://api.anthropic.com",
        max_tokens=1024,
        budget=2048,
        **LEGACY,
    )
    assert bad["thinking"] is None and bad["conflict"] == {"budget": 2048, "ceiling": 1024}
    assert bad["reason"]


def test_sempre_ativo_nao_desliga():
    r = T.resolve_thinking(
        "disabled",
        "medium",
        model="m-always",
        base_url="https://api.anthropic.com",
        max_tokens=4096,
        **ALWAYS,
    )
    assert r["thinking"] is None and r["reason"] == T.REASON_ALWAYS_ON


def test_sem_suporte_omite():
    r = T.resolve_thinking(
        "enabled",
        "medium",
        model="m-none",
        base_url="https://api.anthropic.com",
        max_tokens=4096,
        **NONE,
    )
    assert r["thinking"] is None and r["output_config"] is None


def test_desativado_so_quando_aceito():
    r = T.resolve_thinking(
        "disabled",
        "medium",
        model="m-adapt",
        base_url="https://api.anthropic.com",
        max_tokens=4096,
        **ADAPTIVE,
    )
    assert r["thinking"] == {"type": "disabled"}
    assert r["display"] is None  # display avulso seria inválido


def test_visao_por_id_exato():
    assert T.vision_for(model="m", base_url="https://api.anthropic.com") == "unknown"
    assert T.vision_for(model="m-v", base_url="https://api.anthropic.com", yes={"m-v"}) == "yes"
    assert T.vision_for(model="m-v", base_url="https://x.local", yes={"m-v"}) == "unknown"


def test_normalizacao_rejeita_lixo():
    with pytest.raises(ValueError):
        T.normalize_mode("turbo")
    with pytest.raises(ValueError):
        T.normalize_budget(999)
