"""Controle de pensamento por conversa (TV-1) → args oficiais da API.

Espelha `variation.py`: tabelas explícitas por ID exato de modelo, sem
inferência lexicográfica. O que não está listado é `unknown` (controles
desabilitados, chaves omitidas, preferência preservada).

Formas do SDK instalado (anthropic 1.5.0, tipos verificados):
- adaptativo: thinking={"type": "adaptive", display?} + output_config.effort
- manual:     thinking={"type": "enabled", budget_tokens, display?}
- desligado:  thinking={"type": "disabled"} (sem display — inválido aqui)

Compatibilidade inicial (15/09/2026): nenhuma capacidade por modelo foi
confirmada contra a Models API desta conta; as tabelas nascem vazias e só
ganham entradas com evidência (prova ao vivo ou metadados reais). Conflito
metadados × docs → bloqueia + registra, nunca o permissivo.
"""

from __future__ import annotations

MODE_DEFAULT = "default"
MODE_DISABLED = "disabled"
MODE_ENABLED = "enabled"
MODES = (MODE_DEFAULT, MODE_DISABLED, MODE_ENABLED)
DEFAULT_MODE = MODE_DEFAULT

LEVEL_LOW = "low"
LEVEL_MEDIUM = "medium"
LEVEL_HIGH = "high"
LEVELS = (LEVEL_LOW, LEVEL_MEDIUM, LEVEL_HIGH)
DEFAULT_LEVEL = LEVEL_MEDIUM

LEVEL_LABELS = {LEVEL_LOW: "Baixo", LEVEL_MEDIUM: "Médio", LEVEL_HIGH: "Alto"}

# Presets do produto (não recomendação universal da Anthropic).
BUDGET_PRESETS = (1024, 2048, 4096)
DEFAULT_BUDGET = 1024

EFFORT_BY_LEVEL = {LEVEL_LOW: "low", LEVEL_MEDIUM: "medium", LEVEL_HIGH: "high"}

THINK_MAP_VERSION = "think-map-v1"

# Capacidade por ID exato. Valores: adaptive | legacy | always_on | none.
# Vazio = nada confirmado ainda (ver docstring). Endpoint customizado ou
# modelo fora da tabela → unknown.
CAP_ADAPTIVE_MODELS: frozenset[str] = frozenset()
CAP_LEGACY_MODELS: frozenset[str] = frozenset()
CAP_ALWAYS_ON_MODELS: frozenset[str] = frozenset()
CAP_NO_THINKING_MODELS: frozenset[str] = frozenset()

# Visão por ID exato. Valores: yes | no. Fora da tabela → unknown.
VISION_YES_MODELS: frozenset[str] = frozenset()
VISION_NO_MODELS: frozenset[str] = frozenset()

CAP_ADAPTIVE = "adaptive"
CAP_LEGACY = "legacy"
CAP_ALWAYS_ON = "always_on"
CAP_NONE = "none"
CAP_UNKNOWN = "unknown"

VISION_YES = "yes"
VISION_NO = "no"
VISION_UNKNOWN = "unknown"

REASON_AMBIGUOUS = "Suporte a pensamento ainda não confirmado para este modelo."
REASON_CUSTOM_ENDPOINT = "Suporte a pensamento ainda não confirmado para este endpoint."
REASON_NO_MODEL = "Suporte a pensamento ainda não confirmado."
REASON_ALWAYS_ON = "Este modelo não permite desligar o pensamento."
REASON_BUDGET_CONFLICT = "Orçamento de pensamento ({budget}) não cabe no teto de saída ({ceiling})."
REASON_CONFLICT = "Conflito entre metadados e restrição documentada; bloqueado."

_ANTHROPIC_HOST = "api.anthropic.com"


def normalize_mode(value: str | None) -> str:
    v = (value or "").strip().lower()
    if v not in MODES:
        raise ValueError(f"Modo de pensamento desconhecido: {value!r}. Use: {', '.join(MODES)}.")
    return v


def normalize_level(value: str | None) -> str:
    v = (value or "").strip().lower()
    if v not in LEVELS:
        raise ValueError(f"Nível de pensamento desconhecido: {value!r}. Use: {', '.join(LEVELS)}.")
    return v


def normalize_budget(value: int | None) -> int:
    if value not in BUDGET_PRESETS:
        presets = ", ".join(map(str, BUDGET_PRESETS))
        raise ValueError(f"Orçamento desconhecido: {value!r}. Use: {presets}.")
    return value


def capability_for(
    *,
    model: str,
    base_url: str,
    adaptive=None,
    legacy=None,
    always_on=None,
    none=None,
) -> str:
    """adaptive | legacy | always_on | none | unknown (tabelas injetáveis p/ teste).

    Tabelas omitidas = as do módulo no momento da chamada (permite evolução
    por evidência e monkeypatch em teste); conjuntos explícitos mantêm o
    padrão de injeção dos testes de unidade.
    """
    if adaptive is None:
        adaptive = CAP_ADAPTIVE_MODELS
    if legacy is None:
        legacy = CAP_LEGACY_MODELS
    if always_on is None:
        always_on = CAP_ALWAYS_ON_MODELS
    if none is None:
        none = CAP_NO_THINKING_MODELS
    m = (model or "").strip()
    if not m:
        return CAP_UNKNOWN
    if (_ANTHROPIC_HOST not in (base_url or "")) and base_url:
        return CAP_UNKNOWN
    if m in always_on:
        return CAP_ALWAYS_ON
    if m in adaptive:
        return CAP_ADAPTIVE
    if m in legacy:
        return CAP_LEGACY
    if m in none:
        return CAP_NONE
    return CAP_UNKNOWN


def vision_for(
    *,
    model: str,
    base_url: str,
    yes=None,
    no=None,
) -> str:
    """yes | no | unknown (mesma regra de injeção de `capability_for`)."""
    if yes is None:
        yes = VISION_YES_MODELS
    if no is None:
        no = VISION_NO_MODELS
    m = (model or "").strip()
    if not m:
        return VISION_UNKNOWN
    if (_ANTHROPIC_HOST not in (base_url or "")) and base_url:
        return VISION_UNKNOWN
    if m in yes:
        return VISION_YES
    if m in no:
        return VISION_NO
    return VISION_UNKNOWN


def resolve_thinking(
    mode: str,
    level: str,
    *,
    model: str,
    base_url: str,
    max_tokens: int,
    budget: int = DEFAULT_BUDGET,
    show_summary: bool = False,
    **caps,
) -> dict:
    """Compõe args oficiais ou bloqueia com motivo.

    Retorna dict com thinking/output_config (None = omitir na chamada),
    display (eco informativo da preferência visual — só vale dentro de
    thinking, nunca como campo avulso), state, reason e conflict
    (None quando não há conflito).
    Zero payload incompatível: desconhecido nunca envia chaves.
    """
    mode = normalize_mode(mode)
    level = normalize_level(level)
    cap = capability_for(model=model, base_url=base_url, **caps)

    if mode == MODE_DEFAULT:
        return {
            "thinking": None,
            "output_config": None,
            "display": None,
            "state": MODE_DEFAULT,
            "reason": "",
            "conflict": None,
        }
    if cap == CAP_UNKNOWN:
        m = (model or "").strip()
        reason = (
            REASON_NO_MODEL
            if not m
            else (
                REASON_CUSTOM_ENDPOINT
                if base_url and _ANTHROPIC_HOST not in base_url
                else REASON_AMBIGUOUS
            )
        )
        return {
            "thinking": None,
            "output_config": None,
            "display": None,
            "state": CAP_UNKNOWN,
            "reason": reason,
            "conflict": None,
        }
    if mode == MODE_DISABLED:
        if cap == CAP_ALWAYS_ON:
            return {
                "thinking": None,
                "output_config": None,
                "display": None,
                "state": CAP_ALWAYS_ON,
                "reason": REASON_ALWAYS_ON,
                "conflict": None,
            }
        if cap == CAP_NONE:
            # Nada a desligar: omite em vez de enviar chave inválida.
            return {
                "thinking": None,
                "output_config": None,
                "display": None,
                "state": CAP_NONE,
                "reason": "",
                "conflict": None,
            }
        return {
            "thinking": {"type": "disabled"},
            "output_config": None,
            "display": None,
            "state": MODE_DISABLED,
            "reason": "",
            "conflict": None,
        }
    # MODE_ENABLED
    if cap == CAP_NONE:
        return {
            "thinking": None,
            "output_config": None,
            "display": None,
            "state": CAP_NONE,
            "reason": "",
            "conflict": None,
        }
    if cap == CAP_ALWAYS_ON:
        # Já pensa sempre: só a preferência visual se aplica.
        return {
            "thinking": None,
            "output_config": None,
            "display": "summarized" if show_summary else "omitted",
            "state": CAP_ALWAYS_ON,
            "reason": "",
            "conflict": None,
        }
    if cap == CAP_ADAPTIVE:
        display = "summarized" if show_summary else "omitted"
        return {
            "thinking": {"type": "adaptive", "display": display},
            "output_config": {"effort": EFFORT_BY_LEVEL[level]},
            "display": display,
            "state": CAP_ADAPTIVE,
            "reason": "",
            "conflict": None,
        }
    # CAP_LEGACY
    budget = normalize_budget(budget)
    if not (1024 <= budget < (max_tokens or 0)):
        return {
            "thinking": None,
            "output_config": None,
            "display": None,
            "state": CAP_LEGACY,
            "reason": REASON_BUDGET_CONFLICT.format(budget=budget, ceiling=max_tokens),
            "conflict": {"budget": budget, "ceiling": max_tokens},
        }
    display = "summarized" if show_summary else "omitted"
    return {
        "thinking": {"type": "enabled", "budget_tokens": budget, "display": display},
        "output_config": None,
        "display": display,
        "state": CAP_LEGACY,
        "reason": "",
        "conflict": None,
    }
