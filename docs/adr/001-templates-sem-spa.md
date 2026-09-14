# ADR-001 — Django Templates em vez de React/SPA

Data: 2026-09-14 · Estado: aceita.

Contexto: requisito de stack (Templates + CSS + JS modular, sem Node obrigatório).
Decisão: SSR Django + JS modular (`chat/static/chat/js/`) para streaming/fetch.
Alternativas descartadas: React/SPA (exige toolchain Node, contraria requisito);
HTMX (camada extra sem necessidade — SSE via fetch é simples e didático).
Consequências: bundle zero; Markdown renderizado no cliente com sanitizador próprio.
