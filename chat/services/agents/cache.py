"""CachePlanner M2 (AG-5): modos, TTL, elegibilidade e aplicação no payload.

Cache real do provedor, nunca `functools.lru_cache` (ADR-A3). Desconhecido =
desabilitado (ADR-A5): modo desconhecido vira `disabled`, nunca presumido.

Precedência por campo (C-A2/AG-5.1): override explícito → perfil
(`AgentVersion`) → padrões. TTL padrão `5m`; `1h` só quando explícito.
Nunca dois TTLs/estratégias por chamada: cada plano carrega exatamente um
par (modo, ttl).

Estratégias (verificado no SDK `anthropic==1.5.0`):
- `stable`: breakpoint no fim das instruções aprovadas = `cache_control` no
  ÚLTIMO bloco de texto do `system` (lista; string não carrega marcador).
  Só com ferramentas determinísticas; sem `system` não há breakpoint.
- `conversation`: `cache_control` top-level (o SDK aplica ao último bloco
  cacheável da requisição).

Garantias da aplicação (`apply_to_payload`):
- Nunca marca `thinking` (a chave passa intocada).
- Nunca faz padding: prefixo não elegível recebe zero marcadores.
- Determinística e idempotente: normaliza (remove marcadores prévios) antes
  de aplicar, então reaplicar nunca acumula marcadores.
- Nunca muta o payload de entrada; retorna dict novo.

Métricas (`parse_usage`/`aggregate_usage`): só tokens confirmados em
`Usage` contam (T-G7); ausente = desconhecido (`None`), nunca zero
presumido. Sem estimativa monetária.
"""

from __future__ import annotations

from copy import deepcopy

MODES = ("disabled", "stable", "conversation")
TTLS = ("5m", "1h")

DEFAULT_MODE = "stable"
DEFAULT_TTL = "5m"

CACHE_CONTROL_TYPE = "ephemeral"


def normalize_mode(value) -> str:
    """Modo válido, ou `disabled` quando desconhecido (nunca presume)."""
    v = (value or "").strip().lower() if isinstance(value, str) else ""
    return v if v in MODES else "disabled"


def normalize_ttl(value) -> str:
    """TTL válido, ou `5m` (padrão) quando ausente/desconhecido."""
    v = (value or "").strip().lower() if isinstance(value, str) else ""
    return v if v in TTLS else DEFAULT_TTL


def resolve(profile_mode=None, profile_ttl=None, override=None) -> dict:
    """Precedência override → perfil → padrões, com origem por campo.

    `override`: mapping opcional `{"mode": ..., "ttl": ...}`; valor ausente
    (`None`/`""`) ou inválido cai para o perfil (override inválido nunca
    vence um perfil válido). Retorna `{"mode", "ttl", "origins", "notes"}`.
    """

    def _clean(raw):
        if raw is None:
            return ""
        return raw.strip().lower() if isinstance(raw, str) else ""

    override = override or {}
    notes: list[str] = []
    origins: dict[str, str] = {}

    raw_mode = _clean(override.get("mode"))
    if raw_mode and raw_mode in MODES:
        mode, origins["mode"] = raw_mode, "override"
    else:
        if raw_mode:
            notes.append(f"override mode inválido ({raw_mode!r}); usando perfil")
        v = _clean(profile_mode)
        if v and v in MODES:
            mode, origins["mode"] = v, "agent_version"
        else:
            if v:
                notes.append(f"perfil mode inválido ({v!r}); usando padrão")
            mode = normalize_mode(v or DEFAULT_MODE)
            origins["mode"] = "agent_version" if v in MODES else "default"

    raw_ttl = _clean(override.get("ttl"))
    if raw_ttl and raw_ttl in TTLS:
        ttl, origins["ttl"] = raw_ttl, "override"
    else:
        if raw_ttl:
            notes.append(f"override ttl inválido ({raw_ttl!r}); usando perfil")
        v = _clean(profile_ttl)
        if v and v in TTLS:
            ttl, origins["ttl"] = v, "agent_version"
        else:
            if v:
                notes.append(f"perfil ttl inválido ({v!r}); usando padrão")
            ttl, origins["ttl"] = DEFAULT_TTL, "default"

    if mode == "disabled":
        ttl, origins["ttl"] = normalize_ttl(ttl), origins.get("ttl", "default")
    return {"mode": mode, "ttl": ttl, "origins": origins, "notes": notes}


def _system_text_blocks(system) -> list[dict]:
    """Blocos de texto do system em forma de lista; [] quando não marcável."""
    if not isinstance(system, (list, tuple)):
        return []
    return [b for b in system if isinstance(b, dict) and b.get("type") == "text"]


def plan(
    *,
    mode: str,
    ttl: str,
    system=None,
    messages=None,
    thinking_present: bool = False,
    tools_deterministic: bool = True,
) -> dict:
    """Plano determinístico de cache para uma chamada.

    `thinking_present` nunca bloqueia nem marca pensamento: breakpoints
    vivem em `system`/top-level, e o diagnóstico registra que o pensamento
    não foi marcado. `tools_deterministic=False` torna `stable` não
    elegível (breakpoint só com prefixo determinístico).
    """
    mode = normalize_mode(mode)
    ttl = normalize_ttl(ttl)
    diagnosis = ""
    eligible = False
    strategy = "none"
    if mode == "disabled":
        diagnosis = "disabled"
    elif not _system_text_blocks(system) and not (messages or []):
        diagnosis = "empty_prefix"
    elif mode == "stable" and not _system_text_blocks(system):
        diagnosis = "no_system_prefix"
    elif mode == "stable" and not tools_deterministic:
        diagnosis = "nondeterministic_tools"
    else:
        eligible = True
        strategy = "system_breakpoint" if mode == "stable" else "top_level"
        diagnosis = f"ok:{strategy}"
    if thinking_present and eligible:
        diagnosis += "+thinking_unmarked"
    return {
        "mode": mode,
        "ttl": ttl,
        "eligible": eligible,
        "strategy": strategy,
        "diagnosis": diagnosis,
        "thinking_present": bool(thinking_present),
    }


def _strip_markers(payload: dict) -> dict:
    """Cópia do payload sem nenhum `cache_control` (base p/ aplicação)."""
    base = dict(payload)
    base.pop("cache_control", None)
    system = base.get("system")
    if isinstance(system, list):
        base["system"] = [
            {k: v for k, v in b.items() if k != "cache_control"}
            if isinstance(b, dict)
            else deepcopy(b)
            for b in system
        ]
    return base


def _cache_control(ttl: str) -> dict:
    return {"type": CACHE_CONTROL_TYPE, "ttl": ttl}


def apply_to_payload(payload: dict, plan_: dict) -> dict:
    """Aplica o plano ao payload (dict novo; entrada nunca mutada).

    `disabled`/não elegível → conteúdo idêntico, sem marcadores. `stable` →
    marcador só no último bloco de texto do `system`. `conversation` →
    `cache_control` top-level, blocos intocados. `thinking` e `messages`
    nunca são alterados. Idempotente: reaplicar não acumula marcadores.
    """
    base = _strip_markers(payload)
    if not plan_.get("eligible") or normalize_mode(plan_.get("mode")) == "disabled":
        return base
    mode = normalize_mode(plan_.get("mode"))
    ttl = normalize_ttl(plan_.get("ttl"))
    if mode == "stable":
        system = base.get("system")
        if not _system_text_blocks(system):
            return base
        blocks = [dict(b) if isinstance(b, dict) else b for b in system]
        for block in reversed(blocks):
            if isinstance(block, dict) and block.get("type") == "text":
                block["cache_control"] = _cache_control(ttl)
                break
        base["system"] = blocks
        return base
    if mode == "conversation":
        base["cache_control"] = _cache_control(ttl)
        return base
    return base


def payload_identity_ignored(a: dict, b: dict) -> bool:
    """Verdadeiro quando `a` e `b` diferem só por marcadores de cache."""
    return _strip_markers(a) == _strip_markers(b)


def parse_usage(usage) -> dict:
    """Extrai métricas de cache de `Usage`; ausente = `None` (desconhecido).

    Retorna `{"input_tokens", "cache_creation_input_tokens",
    "cache_read_input_tokens", "cache_creation_detail"}`. `detail` espelha
    o breakdown por TTL (`ephemeral_5m/1h_input_tokens`) quando presente.
    """
    if usage is None:
        return {
            "input_tokens": None,
            "cache_creation_input_tokens": None,
            "cache_read_input_tokens": None,
            "cache_creation_detail": None,
        }
    detail_obj = getattr(usage, "cache_creation", None)
    detail = None
    if detail_obj is not None:
        detail = {
            "ephemeral_5m_input_tokens": getattr(detail_obj, "ephemeral_5m_input_tokens", None),
            "ephemeral_1h_input_tokens": getattr(detail_obj, "ephemeral_1h_input_tokens", None),
        }
        if all(v is None for v in detail.values()):
            detail = None
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
        "cache_creation_detail": detail,
    }


def aggregate_usage(records: list[dict]) -> dict:
    """Agrega métricas de cache sem dupla contagem.

    Fórmulas: `entrada_total = sem_cache + gravada + lida`, onde
    `sem_cache = entrada_total - gravada - lida` (piso zero); `fração_lida =
    lida / entrada_total` (zero protegido → `None`).

    `records`: um dict por CHAMADA/etapa (`parse_usage` + `input_tokens`).
    Passe só registros por chamada — nunca misture o total cumulativo do
    run com as etapas que o compõem (isso contaria tudo duas vezes). Quando
    o registro tiver `"key"`, chaves repetidas contam uma vez só.
    `None` = desconhecido e é ignorado nas somas, nunca tratado como zero.
    """
    seen: set = set()
    calls = 0
    input_sum: int | None = None
    created_sum: int | None = None
    read_sum: int | None = None
    confirmed = 0
    for rec in records or []:
        key = (rec or {}).get("key")
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        calls += 1
        for field in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            value = (rec or {}).get(field)
            if value is None:
                continue
            if field == "input_tokens":
                input_sum = (input_sum or 0) + value
            elif field == "cache_creation_input_tokens":
                created_sum = (created_sum or 0) + value
            else:
                read_sum = (read_sum or 0) + value
        if (rec or {}).get("cache_creation_input_tokens") is not None or (rec or {}).get(
            "cache_read_input_tokens"
        ) is not None:
            confirmed += 1
    created = created_sum or 0
    read = read_sum or 0
    if input_sum is not None:
        total: int | None = input_sum
    elif created_sum is not None or read_sum is not None:
        total = created + read
    else:
        total = None
    fraction = (read / total) if total else None
    return {
        "calls": calls,
        "input_tokens": input_sum,
        "cache_creation_input_tokens": created_sum,
        "cache_read_input_tokens": read_sum,
        "uncached_input_tokens": max(total - created - read, 0) if total is not None else None,
        "cache_read_fraction": fraction,
        "calls_with_confirmed_cache": confirmed,
    }
