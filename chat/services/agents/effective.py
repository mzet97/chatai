"""Config efetiva do agente por conversa (AG-1.3 / C-A2).

Precedência M1 por campo: política/tetos → override da conversa → versão
publicada do perfil → padrões. Tetos de árvore e política global são M4
(`BudgetLedger`); a origem "policy" fica reservada e documentada, sem
override ativo em M1.

A conversa seleciona um _perfil_ (`agent_definition`); aqui resolve-se a
última versão publicada e completa, pinada no snapshot da execução
(publicadas são imutáveis, então a revisão pinada nunca muda de sentido).

Modo `chat` (ou sem perfil/sem versão publicada): None — caminho textual
existente, sem delegação e sem instruções de agente (regressão proibida).
Em M1 o modo `team` NÃO delega (`delegate_to_agent` é M4): roda como
individual e registra isso no snapshot.
"""

from __future__ import annotations

from chat.models_agents import AGENT_MODES
from chat.services import thinking as _thinking
from chat.services import variation as _variation
from chat.services.agents import cache as _cache

DEFAULTS = {
    "model": "",
    "thinking_mode": _thinking.DEFAULT_MODE,
    "thinking_level": _thinking.DEFAULT_LEVEL,
    "thinking_budget": _thinking.DEFAULT_BUDGET,
    "variation_level": _variation.DEFAULT_LEVEL,
    "cache_mode": "stable",
    "cache_ttl": "5m",
    "max_child_runs": 0,
}

CONVERSATION_OVERRIDABLE = (
    "model",
    "thinking_mode",
    "thinking_level",
    "thinking_budget",
    "variation_level",
)


def _conversation_value(conversation, field: str):
    """Valor da conversa, ou None quando ela não sobrescreve (está no padrão)."""
    if field == "model":
        return conversation.preferred_model or None
    if field == "thinking_mode":
        v = conversation.thinking_mode or _thinking.DEFAULT_MODE
        return v if v != _thinking.DEFAULT_MODE else None
    if field == "thinking_level":
        v = conversation.thinking_level or _thinking.DEFAULT_LEVEL
        return v if v != _thinking.DEFAULT_LEVEL else None
    if field == "thinking_budget":
        v = conversation.thinking_budget or _thinking.DEFAULT_BUDGET
        return v if v != _thinking.DEFAULT_BUDGET else None
    if field == "variation_level":
        v = conversation.temperature_level or _variation.DEFAULT_LEVEL
        return v if v != _variation.DEFAULT_LEVEL else None
    return None


def published_version(definition):
    """Última versão publicada e completa do perfil, ou None."""
    from chat.services.agents.profiles import is_complete

    for version in definition.versions.order_by("-revision"):
        if version.published and is_complete(version):
            return version
    return None


def resolve_agent(conversation):
    """Config efetiva do agente, ou None (caminho Chat inalterado).

    Retorna {"definition", "version", "values", "origins", "mode"}. Nunca
    levanta por perfil ausente/arquivado ou sem versão publicada: retorna
    None e o chamador segue o caminho Chat (motivo no snapshot).
    """
    mode = getattr(conversation, "agent_mode", None) or "chat"
    if mode not in AGENT_MODES or mode == "chat":
        return None
    definition_id = getattr(conversation, "agent_definition_id", None)
    if not definition_id:
        return None
    from chat.models_agents import AgentDefinition

    try:
        definition = AgentDefinition.objects.prefetch_related("versions").get(pk=definition_id)
    except AgentDefinition.DoesNotExist:
        return None
    if definition.archived:
        return None
    version = published_version(definition)
    if version is None:
        return None
    values, origins = {}, {}
    for field in CONVERSATION_OVERRIDABLE:
        conv_value = _conversation_value(conversation, field)
        if conv_value is not None:
            values[field], origins[field] = conv_value, "conversation"
        else:
            values[field] = getattr(version, field, DEFAULTS[field]) or DEFAULTS[field]
            origins[field] = "agent_version" if getattr(version, field, None) else "default"
    # Cache (AG-5.1/M2): override da conversa → versão → padrões. Override
    # inválido/ausente cai para a versão; modo desconhecido vira disabled
    # no CachePlanner (nunca presumido).
    for field, valid in (("cache_mode", _cache.MODES), ("cache_ttl", _cache.TTLS)):
        raw = (getattr(conversation, field, "") or "").strip().lower()
        if raw and raw in valid:
            values[field], origins[field] = raw, "conversation"
        else:
            values[field] = getattr(version, field, DEFAULTS[field]) or DEFAULTS[field]
            origins[field] = "agent_version" if getattr(version, field, None) else "default"
    values["max_child_runs"] = getattr(version, "max_child_runs", DEFAULTS["max_child_runs"])
    origins["max_child_runs"] = "agent_version"
    values["task_instructions"] = version.task_instructions or ""
    origins["task_instructions"] = "agent_version" if version.task_instructions else "default"
    values["allowed_tools"] = list(version.allowed_tools or [])
    origins["allowed_tools"] = "agent_version"
    values["allowed_kb_ids"] = list(version.allowed_kb_ids or [])
    origins["allowed_kb_ids"] = "agent_version"
    values["team_delegation"] = "unavailable_m1" if mode == "team" else "not_applicable"
    origins["team_delegation"] = "policy"
    return {
        "definition": definition,
        "version": version,
        "values": values,
        "origins": origins,
        "mode": mode,
    }
