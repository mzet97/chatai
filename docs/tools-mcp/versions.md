# Versões efetivas (verificado em 15/09/2026, neste venv)

| Componente | Versão | Fonte |
|---|---|---|
| `anthropic` (SDK oficial) | 1.5.0 | `pip show`; `tools`/`tool_choice` em create params; `stop_reason` inclui `tool_use`, `pause_turn`, `refusal` |
| `mcp` (SDK oficial) | 2.2.0 | `pip install mcp`; v2: `FastMCP`→`MCPServer` (`mcp.server.mcpserver`), client `mcp.client.stdio.stdio_client`, `mcp.client.streamable_http.streamable_http_client`, `ClientSession(read,write)` + `await initialize()` |
| Django | 5.2.17 | dependência fixada |
| Protocolo MCP observado | via SDK (sessão `initialize` do próprio SDK) | registrado por conexão em `last_protocol_version`; não negociado à mão |
| `httpx2` / `pydantic` / `anyio` | 2.13.0 / 2.13.5 / 4.15.1 | transitivas do ecossistema |

Notas: SDK MCP v2 usa `mcp.client.streamable_http` (underscore, não
`streamablehttp`); servidor demo usa `MCPServer.tool` + `run_stdio_async` /
`run_streamable_http_async`. `mcp_servers` remoto da Anthropic: documentado,
não implementado (ADR-T5).
