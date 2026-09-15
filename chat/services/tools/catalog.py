"""Catálogo autorizado por conversa (M5).

Interseção: prefs da conversa ∩ locais disponíveis ∩ snapshots MCP de
conexões concedidas. Default vazio = sem ferramentas = sem MCP, sem payload.
Catálogo congelado por execução via snapshot (lista de stable_ids + versões).
"""

from __future__ import annotations

from chat.models_tools import ConversationToolPrefs, MCPConnection, ToolCatalogSnapshot
from chat.services.tools.registry import ToolRecord


def get_enabled(conversation) -> list[str]:
    prefs = ConversationToolPrefs.objects.filter(conversation=conversation).first()
    return list(prefs.enabled) if prefs else []


def set_enabled(conversation, stable_ids: list[str]) -> list[str]:
    """Valida contra o catálogo conhecido e persiste. Levanta ValueError."""
    known = {r.stable_id for r in all_available(conversation)}
    unknown = [s for s in stable_ids if s not in known]
    if unknown:
        raise ValueError(f"Ferramentas desconhecidas: {unknown}.")
    prefs, _ = ConversationToolPrefs.objects.get_or_create(conversation=conversation)
    prefs.enabled = sorted(set(stable_ids))
    prefs.save(update_fields=["enabled", "updated_at"])
    return prefs.enabled


def all_available(conversation) -> list[ToolRecord]:
    """Tudo que a conversa *pode* selecionar (para a UI de seleção)."""
    from chat.services.tools.local_tools import all_records

    records = list(all_records())
    records.extend(_mcp_records_for(conversation))
    return records


def authorized_catalog(conversation) -> list[ToolRecord]:
    """Interseção prefs ∩ disponível. Nunca expõe o catálogo inteiro."""
    enabled = set(get_enabled(conversation))
    if not enabled:
        return []
    return [r for r in all_available(conversation) if r.stable_id in enabled]


def _mcp_records_for(conversation) -> list[ToolRecord]:
    """Snapshots MCP de conexões concedidas (owner ou granted_user_ids)."""
    owner = conversation.owner
    granted = []
    for conn in MCPConnection.objects.filter(state="active"):
        if conn.owner_id == owner.pk or owner.pk in (conn.granted_user_ids or []):
            granted.append(conn)
    records = []
    for conn in granted:
        snaps = (
            ToolCatalogSnapshot.objects.filter(connection=conn, revision=conn.revision)
            .order_by("original_name")
            .values("original_name", "description", "input_schema")
        )
        for snap in snaps:
            records.append(
                ToolRecord(
                    stable_id=f"mcp:{conn.uuid}:{snap['original_name']}",
                    origin="mcp",
                    scope=conn.alias,
                    original_name=snap["original_name"],
                    description=(snap["description"] or "")[:1000],
                    input_schema=snap["input_schema"] or {"type": "object"},
                    version=f"r{conn.revision}",
                    approval="require",
                )
            )
    return records
