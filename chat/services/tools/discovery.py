"""Descoberta de ferramentas MCP (admin/ação): conexão → snapshots.

Sem isso, cadastrar a conexão não basta: o catálogo por conversa só lista
ferramentas com snapshot na revisão atual da conexão. Sem rede, sem shell
livre, sem segredos (bearer sai do cofre na hora, nunca do config).
"""

from __future__ import annotations

import asyncio
import hashlib
import json

from django.utils import timezone

from chat.models_tools import MCPConnection, ToolCatalogSnapshot
from chat.services.tools import mcp_client
from chat.services.tools.credentials import retrieve


def _schema_hash(schema: dict) -> str:
    return hashlib.sha256(
        json.dumps(schema, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def refresh_connection(conn_id: int, *, timeout_s: float = 20.0) -> dict:
    """Redescobre via transporte configurado e troca os snapshots da revisão.

    Retorna {"tools": N, "protocol_version": ...}. Erro de transporte levanta
    RuntimeError com mensagem sanitizada e registra em last_error.
    """
    conn = MCPConnection.objects.get(pk=conn_id)
    cfg = conn.config or {}
    try:
        if conn.transport == "stdio":
            command = cfg.get("command", "")
            if not command:
                raise ValueError("config.command ausente (caminho absoluto).")
            config = mcp_client.StdioConfig(
                command=command,
                args=tuple(cfg.get("args", [])),
                extra_env=dict(cfg.get("env", {})),
            )
            info = asyncio.run(mcp_client.list_tools_stdio(config, timeout_s))
        elif conn.transport == "streamable_http":
            url = cfg.get("url", "")
            if not url:
                raise ValueError("config.url ausente.")
            token = retrieve(conn.owner_id, conn.credential_ref) if conn.credential_ref else ""
            if conn.credential_ref and not token:
                raise ValueError("credencial ausente no cofre.")
            config = mcp_client.HttpConfig(
                url=url,
                bearer_token=token or "",
                allow_loopback_http=bool(cfg.get("allow_loopback_http", False)),
            )
            info = asyncio.run(mcp_client.list_tools_http(config, timeout_s))
        else:
            raise ValueError(f"transporte desconhecido: {conn.transport!r}.")
    except Exception as exc:
        conn.last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
        conn.last_checked_at = timezone.now()
        conn.save(update_fields=["last_error", "last_checked_at"])
        raise RuntimeError(conn.last_error) from exc

    recs = mcp_client.records_for(str(conn.uuid), conn.alias, info, version="r1")
    ToolCatalogSnapshot.objects.filter(connection=conn, revision=conn.revision).delete()
    ToolCatalogSnapshot.objects.bulk_create(
        ToolCatalogSnapshot(
            connection=conn,
            original_name=r.original_name,
            anthropic_name=r.anthropic_name,
            description=r.description,
            input_schema=r.input_schema,
            schema_hash=_schema_hash(r.input_schema),
            revision=conn.revision,
        )
        for r in recs
    )
    conn.last_protocol_version = info.protocol_version or ""
    conn.last_error = ""
    conn.last_checked_at = timezone.now()
    conn.save(update_fields=["last_protocol_version", "last_error", "last_checked_at"])
    return {"tools": len(recs), "protocol_version": conn.last_protocol_version}
