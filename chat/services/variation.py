"""Nível de variação por conversa (Baixo/Médio/Alto) → `temperature`.

Mapeamento (escolha do produto) e compatibilidade centralizados no backend.
O SDK instalado (anthropic 1.5.0) removeu `temperature` dos métodos Messages;
para modelos que ainda aceitam, o valor vai via `extra_body` (passthrough).

Compatibilidade (verificado em 14/09/2026):
- Docs do provedor (platform.claude.com/docs/en/api/messages/create):
  `temperature` depreciado; modelos após o Claude Opus 4.6 rejeitam qualquer
  valor ≠ 1.0 com 400.
- Prova ao vivo (chamada rejeitada, sem custo): Sonnet 5 + temperature=0.5 →
  400 "`temperature` is deprecated for this model."
- Tabela explícita por ID exato — sem inferência lexicográfica. O que não está
  listado é `unknown` (controle desabilitado, chave omitida, preferência
  preservada para quando voltar a modelo compatível).
- Endpoint customizado (fora de api.anthropic.com): `unknown`, pois o contrato
  do provedor não pode ser confirmado.
- O app nunca envia `thinking`; sem conflito possível por essa via.
"""

from __future__ import annotations

LEVEL_LOW = "low"
LEVEL_MEDIUM = "medium"
LEVEL_HIGH = "high"
LEVELS = (LEVEL_LOW, LEVEL_MEDIUM, LEVEL_HIGH)
DEFAULT_LEVEL = LEVEL_MEDIUM

LEVEL_LABELS = {LEVEL_LOW: "Baixo", LEVEL_MEDIUM: "Médio", LEVEL_HIGH: "Alto"}

# Escolha do produto (não escala de inteligência; sem promessa de determinismo).
TEMPERATURE_BY_LEVEL = {LEVEL_LOW: 0.2, LEVEL_MEDIUM: 0.5, LEVEL_HIGH: 0.8}

TEMP_MAP_VERSION = "temp-map-v1"

# Modelos que comprovadamente aceitam temperature (geração ≤ Opus 4.6).
TEMPERATURE_SUPPORTED_MODELS = frozenset(
    {
        "claude-opus-4-6",
        "claude-opus-4-5-20251101",
        "claude-sonnet-4-5-20250929",
        "claude-haiku-4-5-20251001",
    }
)

# Modelos que comprovadamente rejeitam (geração 5; Sonnet 5 verificado ao vivo).
TEMPERATURE_UNSUPPORTED_MODELS = frozenset(
    {
        "claude-sonnet-5",
        "claude-opus-5",
        "claude-fable-5",
        "claude-fable-5-1",
    }
)

STATE_SUPPORTED = "supported"
STATE_UNSUPPORTED = "unsupported"
STATE_UNKNOWN = "unknown"

REASON_AMBIGUOUS = "Suporte a este ajuste ainda não confirmado."
REASON_UNAVAILABLE = "Este ajuste não está disponível no modelo selecionado."
REASON_CUSTOM_ENDPOINT = "Suporte a este ajuste ainda não confirmado para este endpoint."
REASON_NO_MODEL = "Suporte a este ajuste ainda não confirmado."

_ANTHROPIC_HOST = "api.anthropic.com"


def normalize_level(value: str | None) -> str:
    """Valida o nível vindo do cliente; levanta ValueError em valor desconhecido."""
    v = (value or "").strip().lower()
    if v not in LEVELS:
        raise ValueError(f"Nível de variação desconhecido: {value!r}. Use: {', '.join(LEVELS)}.")
    return v


def temperature_for(level: str) -> float:
    """Mapeamento exato nível → temperature (só aceita níveis válidos)."""
    return TEMPERATURE_BY_LEVEL[normalize_level(level)]


def resolve_compatibility(*, model: str, base_url: str) -> tuple[str, str]:
    """Três estados: supported / unsupported / unknown (+ motivo acessível)."""
    m = (model or "").strip()
    if not m:
        return STATE_UNKNOWN, REASON_NO_MODEL
    if (_ANTHROPIC_HOST not in (base_url or "")) and base_url:
        return STATE_UNKNOWN, REASON_CUSTOM_ENDPOINT
    if m in TEMPERATURE_UNSUPPORTED_MODELS:
        return STATE_UNSUPPORTED, REASON_UNAVAILABLE
    if m in TEMPERATURE_SUPPORTED_MODELS:
        return STATE_SUPPORTED, ""
    return STATE_UNKNOWN, REASON_AMBIGUOUS


def resolve_temperature(
    level: str, *, model: str, base_url: str
) -> tuple[float | None, str, str]:
    """(valor | None, estado, motivo). Valor só quando suportado; nunca None silencioso."""
    state, reason = resolve_compatibility(model=model, base_url=base_url)
    if state != STATE_SUPPORTED:
        return None, state, reason
    return temperature_for(level), state, ""
