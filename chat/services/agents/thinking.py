"""Pensamento por agente (M3 / AG-4): resolução, validação e replay por agente.

Reutiliza `chat/services/thinking.py` (tabelas por ID exato, sem inferência
lexicográfica) e `chat/services/thinking_stream/` (protocolo vs painel).
Desconhecido = desabilitado (ADR-A5): modelo fora das tabelas nunca recebe
chaves de pensamento; a preferência do perfil é preservada e o motivo vai
para o snapshot.

- Precedência dos valores (AG-1.3/C-A2): já resolvida em `effective.py`
  (conversa → versão publicada → padrões, com origem por campo). Aqui só se
  consome `values`/`origins` para compor a chamada e expor o bloco efetivo.
- `thinking_show_summary` (resumo do painel) continua na conversa em M3: não
  há campo por versão; a origem registrada é sempre "conversation".
- Replay separado (AG-4.2/T-G6): cada agente só continua do próprio log; a
  resposta do filho nunca vira pensamento do pai (vira conteúdo `user`, que o
  pai revalida como dado, nunca como ordem).
- Painel "Resumo do pensamento": única fonte da UI é a projeção
  (`store.project_panel`); signatures/segmentos brutos nunca são exibidos e o
  resumo só usa o texto disponibilizado pelo provedor.

Sem chamadas pagas, sem Django: puro e testável sem banco.
"""

from __future__ import annotations

from chat.services import thinking as _thinking
from chat.services.thinking_stream import replay as _replay

THINKING_FIELDS = ("thinking_mode", "thinking_level", "thinking_budget")

_VALIDATORS = {
    "thinking_mode": _thinking.normalize_mode,
    "thinking_level": _thinking.normalize_level,
    "thinking_budget": _thinking.normalize_budget,
}


def validate_fields(body: dict) -> dict[str, str]:
    """Valida campos de pensamento de um PATCH de versão de agente.

    Retorna {campo: mensagem} (vazio = válido). Só valida campos presentes;
    ausente = sem alteração, nunca erro. Tipos errados (ex.: lista) também
    viram erro em vez de quebrar o `setattr`/`save`.
    """
    errors: dict[str, str] = {}
    for field, normalizer in _VALIDATORS.items():
        if field in (body or {}):
            try:
                normalizer(body[field])
            except Exception as exc:  # qualquer rejeição vira 400, nunca 500
                errors[field] = str(exc) or f"Valor inválido para {field}."
    return errors


def resolve_for_agent(
    *,
    values: dict,
    origins: dict,
    model: str,
    base_url: str,
    max_tokens: int,
    show_summary: bool = False,
    **caps,
) -> dict:
    """Compõe o pensamento efetivo do agente para uma chamada.

    `values`/`origins`: saída de `effective.resolve_agent` (precedência já
    aplicada). `caps`: tabelas de capacidade injetáveis (testes); omitidas,
    valem as tabelas reais (hoje vazias → `unknown`).
    Retorna {"think" (saída de `thinking.resolve_thinking`), "effective",
    "origins", "active"}. `ValueError` de dado legado propaga (o chamador
    decide o fallback); conflito de orçamento vem em `think["conflict"]`.
    """
    mode = values.get("thinking_mode") or _thinking.DEFAULT_MODE
    level = values.get("thinking_level") or _thinking.DEFAULT_LEVEL
    budget = values.get("thinking_budget", _thinking.DEFAULT_BUDGET)
    think = _thinking.resolve_thinking(
        mode,
        level,
        model=model,
        base_url=base_url,
        max_tokens=max_tokens,
        budget=budget,
        show_summary=bool(show_summary),
        **caps,
    )
    active = think["thinking"] is not None or think["output_config"] is not None
    return {
        "think": think,
        "effective": {
            "mode": mode,
            "level": level,
            "budget": budget,
            "display": think["display"],
            "show_summary": bool(show_summary),
        },
        "origins": {
            "mode": origins.get("thinking_mode", "default"),
            "level": origins.get("thinking_level", "default"),
            "budget": origins.get("thinking_budget", "default"),
            "show_summary": "conversation",
        },
        "active": active,
    }


def replay_for(logs_by_agent: dict | None, agent_id: str, tool_results: list[dict]) -> list[dict]:
    """Mensagens de continuação só do log do próprio agente (AG-4.2).

    - Sem `tool_results`: [] (nada a continuar).
    - Log do agente ausente/desconhecido: [] — nunca toma emprestado o log de
      outro agente (pai/filho jamais se misturam).
    - Log presente mas sem signature/redacted: propaga `ReplayError` (falha
      explícita > bloco falso que o provedor rejeitaria).
    """
    if not tool_results:
        return []
    log = (logs_by_agent or {}).get(agent_id)
    if log is None:
        return []
    return _replay.replay_messages(log, tool_results)


def child_answer_as_user_content(
    *,
    summary: str,
    limitations: str = "",
) -> dict:
    """Converte o retorno validado do filho em conteúdo `user` do pai.

    A resposta do filho nunca vira bloco `thinking`/`signature` do pai
    (T-G6): o pai recebe texto comum e revalida como dado. Sem `signature`,
    sem replay implícito — só o que o filho disponibilizou em texto.
    """
    parts = [f"Retorno do especialista: {(summary or '').strip()}".strip()]
    if (limitations or "").strip():
        parts.append(f"Limitações relatadas: {limitations.strip()}")
    return {"role": "user", "content": [{"type": "text", "text": "\n".join(parts)}]}
