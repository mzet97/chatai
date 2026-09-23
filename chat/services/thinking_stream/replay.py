"""Replay do pensamento p/ continuação/retomada, com tool_result (M2).

Regras da API (a validar contra o SDK real na integração; conservadoras aqui):

- Blocos thinking com signature devem ser reenviados ÍNTEGROS na retomada;
  sem signature válida o provedor rejeita — então `replay_messages` só
  reemite o bloco thinking quando `store.has_signature` é True.
- Pensamento redacted/ausente nunca é reconstruído nem inventado: retorna
  erro explícito em vez de bloco falso.
- `tool_result` entra como mensagem `user` subsequente ao bloco thinking +
  tool_use, espelhando o padrão já usado em `chat/services/tools/chat_loop.py`
  (`error_result` / blocos de continuação). `build_continuation_blocks`
  monta exatamente esses blocos tool_result.

Tudo puro (listas/dicts), sem Django: a integração em `generation.resume_run`
só precisa concatenar o retorno ao `state.messages` restaurado.
"""

from __future__ import annotations

from chat.services.thinking_stream import store as _store


class ReplayError(Exception):
    pass


def build_continuation_blocks(tool_results: list[dict]) -> list[dict]:
    """Monta blocos tool_result a partir de [{tool_use_id, content, is_error?}].

    Levanta ReplayError em entrada sem tool_use_id (falha explícita > bloco
    falso que o provedor rejeitaria de forma obscura).
    """
    blocks = []
    for item in tool_results:
        tool_use_id = (item or {}).get("tool_use_id", "")
        if not tool_use_id:
            raise ReplayError("tool_result sem tool_use_id não pode ser remontado.")
        block: dict = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": (item or {}).get("content", ""),
        }
        if (item or {}).get("is_error"):
            block["is_error"] = True
        blocks.append(block)
    return blocks


def replay_messages(
    log: dict | None,
    tool_results: list[dict],
    *,
    thinking_block: dict | None = None,
) -> list[dict]:
    """Reconstrói mensagens de continuação: assistant(thinking) + user(tool_result).

    - `log`: protocolo persistido (fonte da signature). Redacted/ausente →
      ReplayError (nunca inventar pensamento).
    - `thinking_block`: override explícito (ex.: bloco capturado ao vivo);
      quando omitido, o bloco é derivado do protocolo.
    Retorna [] quando não há tool_results (nada a continuar).
    """
    if not tool_results:
        return []
    if log is None or log.get("redacted"):
        raise ReplayError("Pensamento oculto ou ausente: replay indisponível.")
    if not _store.has_signature(log):
        raise ReplayError("Sem signature válida: o provedor rejeitaria a continuação.")
    block = dict(thinking_block) if thinking_block is not None else {
        "type": "thinking",
        "thinking": _store.protocol_text(log),
        "signature": "".join(s.get("signature", "") for s in log.get("segments", [])),
    }
    return [
        {"role": "assistant", "content": [block]},
        {"role": "user", "content": build_continuation_blocks(tool_results)},
    ]
