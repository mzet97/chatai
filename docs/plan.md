# Plano de entregas

Sequência com dependências. Cada etapa termina com comportamento demonstrável,
arquivos relevantes, comandos/testes e limitações (ver `docs/verification.md`).

## Etapa A — Pesquisa, especificação, arquitetura, dados, plano ✓ (este documento + specs)
Entrega: `docs/{research,spec,architecture,plan,tasks}.md`, `docs/adr/`, `.env.example`.

## Etapa B — Projeto executável, login, CRUD persistente (sem IA)
- Django 5.2.17 + ASGI/Uvicorn, SQLite `data/chat.sqlite3`, login, bootstrap local.
- CRUD conversas/mensagens, busca paginada, arquivar/excluir.
- Depende de: A. Desbloqueia: C, D.

## Etapa C — Configuração, credenciais, diagnóstico, catálogo
- `configuration.py`, `anthropic_client.py`, tela de config, Keychain via keyring.
- Diagnóstico em 4 passos; `model_catalog.py` com paginação + cache.
- Depende de: B. Desbloqueia: D.

## Etapa D — Contexto, contagem, geração textual
- `context_builder.py` (reconstrução + orçamento + count_tokens), `generation.py`
  não-streaming persistida, painel de inspeção (snapshot).
- Depende de: C. Desbloqueia: E.

## Etapa E — Streaming, cancelamento, recuperação, idempotência
- SSE `StreamingHttpResponse`, checkpoints, cancel/interrupt/abandon, update atômico,
  retry explícito. Testes de concorrência em SQLite arquivo.
- Depende de: D. Desbloqueia: F.

## Etapa F — Interface final, Markdown seguro, tokens, exportação/backup
- UI pt-BR completa, Markdown seguro, painel didático, export JSON/Markdown,
  comando `backup_db`/`restore_db`.
- Depende de: E. Desbloqueia: G.

## Etapa G — Testes completos, segurança, docs de execução, guia didático
- Suíte verde sem rede, revisão RNF-01, README, CLAUDE.md, learning-guide,
  verification com evidências. Depende de: F.
