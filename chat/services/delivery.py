"""Entrega da resposta: streaming (progressivo) ou completa (só ao final).

O controle altera COMO a resposta chega ao navegador, nunca modelo,
temperatura, effort, system prompt, histórico, limite de saída ou política.
O provedor continua sendo consumido pelo mesmo `client.messages.stream`;
o modo controla apenas a entrega ao navegador (SDD Streaming §4).
"""

from __future__ import annotations

STREAMING = "streaming"
COMPLETE = "complete"
MODES = (STREAMING, COMPLETE)

# Sem gate administrativo de revisão prévia no projeto (a política corporativa
# atua como system prompt, não como inspeção obrigatória da saída): o modo
# efetivo é sempre o solicitado. O motivo fica registrado no snapshot.
NO_REVIEW_GATE_REASON = "sem revisao previa obrigatoria"


def normalize_mode(value: object) -> str:
    """Valida o modo vindo do cliente/banco. Levanta ValueError fora do enum."""
    mode = str(value or "").strip().lower()
    if mode not in MODES:
        raise ValueError(f"Modo inválido: {value!r}. Use 'streaming' ou 'complete'.")
    return mode


def resolve_delivery(requested: object) -> tuple[str, str | None]:
    """Retorna (modo_efetivo, motivo). Hoje sem bloqueio administrativo."""
    mode = normalize_mode(requested) if requested else STREAMING
    return mode, None
