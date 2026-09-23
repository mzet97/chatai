"""Contexto de execução confiável (servidor; nunca vem da IA)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionContext:
    user_id: int
    conversation_id: int
    tool_use_id: str = ""  # invocação atual; compõe a chave única de operação
    run_uuid: str = ""  # geração dona (para vincular runs RAG)
