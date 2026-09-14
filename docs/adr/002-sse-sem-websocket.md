# ADR-002 — SSE via StreamingHttpResponse em vez de WebSocket

Data: 2026-09-14 · Estado: aceita.

Contexto: resposta progressiva unidirecional servidor→cliente.
Decisão: `StreamingHttpResponse` com `text/event-stream`, consumido por `fetch` +
`ReadableStream`. Sem Channels/Daphne/Redis.
Alternativas descartadas: WebSocket (bidirecionalidade desnecessária; exige Channels
para Django); polling (latência e custo).
Consequências: cancelamento = abort do `fetch` no cliente + fechamento do stream
upstream no servidor; funciona com 1 worker Uvicorn.
