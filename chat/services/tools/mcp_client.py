"""Cliente MCP controlado pelo backend (T6/T10/T11).

- Sessões abrem→usam→fecham na MESMA tarefa (nunca guardadas entre loops).
- stdio: caminho absoluto, sem shell, env mínimo explícito (sem segredos).
- Sem handshake manual: `ClientSession` + `initialize()` do SDK.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any

from chat.services.tools.registry import ToolRecord

SECRET_NAMES = (
    "ANTHROPIC_API_KEY",
    "DJANGO_SECRET_KEY",
    "CHAT_DB_PATH",
    "CHAT_DOTENV_PATH",
)

BASE_ENV = {
    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    "PYTHONUTF8": "1",
    "PYTHONNOUSERSITE": "1",
}


@dataclass(frozen=True)
class StdioConfig:
    command: str  # absoluto, sem shell
    args: tuple[str, ...] = ()
    cwd: str = ""
    extra_env: dict[str, str] = field(default_factory=dict)


@dataclass
class DiscoveredTools:
    protocol_version: str
    tools: list[dict[str, Any]]


def build_env(extra: dict[str, str]) -> dict[str, str]:
    """Env mínimo explícito. Segredos nunca entram, mesmo se herdáveis."""
    env = dict(BASE_ENV)
    for key, value in extra.items():
        if key in SECRET_NAMES or not key or not isinstance(value, str):
            continue
        env[key] = value
    for banned in SECRET_NAMES:
        env.pop(banned, None)
    return env


def _params(config: StdioConfig):
    from mcp.client.stdio import StdioServerParameters

    if not os.path.isabs(config.command):
        raise ValueError(f"Comando stdio deve ser absoluto: {config.command!r}.")
    return StdioServerParameters(
        command=config.command,
        args=list(config.args),
        cwd=config.cwd or None,
        env=build_env(config.extra_env),
    )


async def _run_stdio(config: StdioConfig, timeout_s: float, operation):
    """Sessão contida: abrir→usar→fechar na mesma tarefa, com `async with`
    aninhados (gerenciamento manual de CM + exceção travava o task group)."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async def _body():
        async with stdio_client(_params(config)) as (read, write):
            async with ClientSession(read, write) as session:
                result = await session.initialize()
                protocol = getattr(result, "protocol_version", "") or getattr(
                    result, "protocolVersion", ""
                )
                return await operation(session, protocol)

    return await asyncio.wait_for(_body(), timeout=timeout_s)


async def list_tools_stdio(config: StdioConfig, timeout_s: float = 20.0) -> DiscoveredTools:
    from mcp.types import PaginatedRequestParams

    from chat.services.tools import limits as _limits

    async def _list(session, protocol: str):
        tools = []
        cursor = None
        while True:
            params = PaginatedRequestParams(cursor=cursor) if cursor else None
            page = await session.list_tools(params=params)
            for tool in page.tools:
                tools.append(
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "inputSchema": dict(tool.input_schema or {"type": "object"}),
                        "annotations": (tool.annotations.model_dump() if tool.annotations else {}),
                    }
                )
            cursor = getattr(page, "next_cursor", None)
            if not cursor:
                break
        return DiscoveredTools(protocol_version=protocol, tools=tools)

    return await _run_stdio(config, min(timeout_s, _limits.TOOL_TIMEOUT_S), _list)


async def call_tool_stdio(
    config: StdioConfig, name: str, args: dict, timeout_s: float = 20.0
) -> dict:
    """Executa e normaliza: texto concatenado, `isError` preservado, resto
    não suportado (imagens/áudio/links) identificado, sem download."""
    from chat.services.tools import executor as _executor
    from chat.services.tools import limits as _limits

    async def _call(session, protocol: str):
        return await session.call_tool(name, args)

    try:
        result = await _run_stdio(config, min(timeout_s, _limits.TOOL_TIMEOUT_S), _call)
    except TimeoutError:
        return {
            "ok": False,
            "error": {"code": "timeout", "message": "Servidor MCP excedeu o tempo."},
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": {"code": "transport", "message": f"Falha no MCP: {type(exc).__name__}."},
        }
    texts: list[str] = []
    unsupported = False
    for block in result.content or []:
        btype = getattr(block, "type", "")
        if btype == "text":
            texts.append(getattr(block, "text", ""))
        else:
            unsupported = True
    structured = getattr(result, "structuredContent", None)
    if structured and not texts:
        import json

        texts.append(json.dumps(structured, ensure_ascii=False, sort_keys=True))
    text = "\n".join(texts)
    if unsupported:
        text += "\n[parte da resposta não suportada nesta versão: só texto]"
    text, truncated = _executor.normalize_result(text)
    failed = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
    out: dict[str, Any] = {"ok": not failed, "text": text}
    if out["ok"] is False:
        out = {
            "ok": False,
            "error": {"code": "mcp_error", "message": text or "Erro no servidor MCP."},
        }
    if truncated:
        out["truncated"] = True
    return out


class HttpAuthMissing(Exception):
    pass


@dataclass(frozen=True)
class HttpConfig:
    url: str
    bearer_token: str = ""  # valor resolvido do cofre na hora; nunca logado
    allow_loopback_http: bool = False  # só demonstração local explícita


async def _run_http(config: HttpConfig, timeout_s: float, operation):
    """Streamable HTTP via SDK, mesma contenção de sessão do stdio."""
    import asyncio

    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from chat.services.tools import ssrf as _ssrf

    target = _ssrf.validate_http_target(
        config.url, allow_loopback_http=config.allow_loopback_http
    )
    headers = {}
    if config.bearer_token:
        headers["Authorization"] = "Bearer " + config.bearer_token

    async def _body():
        import httpx2

        # Redirects: o SDK só segue mesma origem (sem downgrade); aqui o
        # destino inicial já passou pelo guarda SSRF.
        async with httpx2.AsyncClient(headers=headers, timeout=timeout_s) as http:
            async with streamable_http_client(target, http_client=http) as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    result = await session.initialize()
                    protocol = getattr(result, "protocol_version", "")
                    return await operation(session, protocol)

    return await asyncio.wait_for(_body(), timeout=timeout_s)


async def list_tools_http(config: HttpConfig, timeout_s: float = 20.0) -> DiscoveredTools:
    from mcp.types import PaginatedRequestParams

    from chat.services.tools import limits as _limits

    async def _list(session, protocol: str):
        tools = []
        cursor = None
        while True:
            params = PaginatedRequestParams(cursor=cursor) if cursor else None
            page = await session.list_tools(params=params)
            for tool in page.tools:
                tools.append(
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "inputSchema": dict(tool.input_schema or {"type": "object"}),
                        "annotations": (
                            tool.annotations.model_dump() if tool.annotations else {}
                        ),
                    }
                )
            cursor = getattr(page, "next_cursor", None)
            if not cursor:
                break
        return DiscoveredTools(protocol_version=protocol, tools=tools)

    return await _run_http(config, min(timeout_s, _limits.TOOL_TIMEOUT_S), _list)


async def call_tool_http(
    config: HttpConfig, name: str, args: dict, timeout_s: float = 20.0
) -> dict:
    from chat.services.tools import executor as _executor
    from chat.services.tools import limits as _limits

    async def _call(session, protocol: str):
        return await session.call_tool(name, args)

    try:
        result = await _run_http(config, min(timeout_s, _limits.TOOL_TIMEOUT_S), _call)
    except TimeoutError:
        return {
            "ok": False,
            "error": {"code": "timeout", "message": "Servidor MCP excedeu o tempo."},
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": {"code": "transport", "message": f"Falha no MCP: {type(exc).__name__}."},
        }
    texts: list[str] = []
    unsupported = False
    for block in result.content or []:
        btype = getattr(block, "type", "")
        if btype == "text":
            texts.append(getattr(block, "text", ""))
        else:
            unsupported = True
    structured = getattr(result, "structured_content", None)
    if structured and not texts:
        import json

        texts.append(json.dumps(structured, ensure_ascii=False, sort_keys=True))
    text = "\n".join(texts)
    if unsupported:
        text += "\n[parte da resposta não suportada nesta versão: só texto]"
    text, truncated = _executor.normalize_result(text)
    failed = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
    out: dict[str, Any] = {"ok": not failed, "text": text}
    if out["ok"] is False:
        out = {
            "ok": False,
            "error": {"code": "mcp_error", "message": text or "Erro no servidor MCP."},
        }
    if truncated:
        out["truncated"] = True
    return out


def records_for(
    connection_uuid: str, alias: str, info: DiscoveredTools, *, version: str
) -> list[ToolRecord]:
    """Mapeamento persistível (conexão, original) → nome Anthropic."""
    recs = []
    for tool in info.tools:
        recs.append(
            ToolRecord(
                stable_id=f"mcp:{connection_uuid}:{tool['name']}",
                origin="mcp",
                scope=alias,
                original_name=tool["name"],
                description=tool.get("description", "")[:1000],
                input_schema=tool.get("inputSchema") or {"type": "object"},
                version=version,
                approval="require",
            )
        )
    return recs
