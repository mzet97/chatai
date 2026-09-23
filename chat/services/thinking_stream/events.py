"""Protocolo SSE do pensamento (M2) + extração do stream bruto do SDK.

Eventos (mesmo envelope de `generation.execute_run`: run_id + seq monotônica,
aplicado por `with_envelope` para compatibilidade com o `_emit` existente):

- thinking_started {display: summarized|omitted|disabled}
- thinking_delta {text} — fragmento incremental do resumo do pensamento
- thinking_completed {text} — texto final autorizado do resumo
- thinking_redacted {reason} — provedor ocultou o pensamento (ex.: modelo
  always_on sem resumo, ou bloco redacted_thinking); UI mostra aviso, nunca
  conteúdo inventado.

Extração (`iter_thinking_events`): espelha `generation._iter_stream_text`,
que hoje só extrai text_delta/citations_delta de `content_block_delta` e
ignora o resto. Esta função extrai `thinking_delta` e `signature_delta`
sem alterar o extrator atual — a integração futura chama as duas.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

EVENT_STARTED = "thinking_started"
EVENT_DELTA = "thinking_delta"
EVENT_COMPLETED = "thinking_completed"
EVENT_REDACTED = "thinking_redacted"

DISPLAY_SUMMARIZED = "summarized"
DISPLAY_OMITTED = "omitted"
DISPLAY_DISABLED = "disabled"


def make_started(*, display: str) -> dict:
    return {"type": EVENT_STARTED, "display": display}


def make_delta(*, text: str) -> dict:
    return {"type": EVENT_DELTA, "text": text or ""}


def make_completed(*, text: str) -> dict:
    return {"type": EVENT_COMPLETED, "text": text or ""}


def make_redacted(*, reason: str) -> dict:
    return {"type": EVENT_REDACTED, "reason": reason or ""}


def with_envelope(event: dict, *, run_id: str, seq: int) -> dict:
    """Aplica o envelope padrão (run_id + seq) sem mutar o evento original."""
    return {"run_id": str(run_id), "seq": seq, **event}


def iter_thinking_events(stream_events: Any) -> Iterator[tuple[str, Any]]:
    """Extrai (kind, payload) de pensamento de eventos brutos do SDK.

    Yields:
      ("thinking", str) — fragmento de thinking_delta.
      ("signature", str) — fragmento de signature_delta (protocolo, não exibido).
      ("redacted", True) — bloco redacted_thinking (pensamento oculto).

    Eventos de texto/citação são ignorados aqui (dono: `_iter_stream_text`).
    Duck-typing proposital: aceita objetos do SDK real e doubles de teste.
    """
    for event in stream_events:
        if getattr(event, "type", "") != "content_block_delta":
            continue
        delta = getattr(event, "delta", None)
        dtype = getattr(delta, "type", "")
        if dtype == "thinking_delta":
            yield ("thinking", getattr(delta, "thinking", "") or "")
        elif dtype == "signature_delta":
            yield ("signature", getattr(delta, "signature", "") or "")
        elif dtype == "redacted_thinking_delta":
            yield ("redacted", True)
