"""Servidor MCP demonstrativo `study-lessons` (T1/M3).

Conjunto fechado e versionado de lições fictícias. Aceita IDs do conjunto,
nunca caminhos. Execução:
  stdio:  python mcp_servers/study_lessons/server.py
  http:   python mcp_servers/study_lessons/server.py --http --port 8139
"""

from __future__ import annotations

import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

LESSONS_VERSION = "lessons-v1"

LESSONS = {
    "tdd": {
        "title": "TDD em loops curtos",
        "text": "Lição fictícia: escreva o teste, veja falhar, implemente o mínimo.",
    },
    "sse": {
        "title": "SSE sem buffering",
        "text": "Lição fictícia: StreamingHttpResponse com iterador assíncrono.",
    },
    "sqlite": {
        "title": "SQLite concorrente",
        "text": "Lição fictícia: transações curtas, sem select_for_update.",
    },
}

server = MCPServer("study-lessons")


@server.tool(description="Lista os IDs e títulos das lições disponíveis.")
def list_lessons() -> str:
    return "\n".join(f"{lid}: {info['title']}" for lid, info in LESSONS.items())


@server.tool(description="Lê o texto de uma lição pelo ID do conjunto.")
def read_lesson(lesson_id: str) -> str:
    info = LESSONS.get(lesson_id)
    if info is None:
        # ToolError (previsto) → is_error=True com a mensagem; crash daria só
        # "Error executing tool" sem detalhe (semântica preservada no cliente).
        raise ToolError(f"Lição desconhecida: {lesson_id!r}. Use list_lessons.")
    return f"# {info['title']}\n\n{info['text']}"


def main(argv: list[str]) -> None:
    import anyio

    if "--http" in argv:
        port = 8139
        if "--port" in argv:
            port = int(argv[argv.index("--port") + 1])
        import functools

        anyio.run(
            functools.partial(
                server.run_streamable_http_async, host="127.0.0.1", port=port
            )
        )
    else:
        anyio.run(server.run_stdio_async)


if __name__ == "__main__":
    main(sys.argv[1:])
