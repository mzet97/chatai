# CLAUDE.md — claude-chat-local

App Django 5.2 + `anthropic==1.5.0` (AsyncAnthropic), SQLite `data/chat.sqlite3`,
SSE via `StreamingHttpResponse`, 1 worker uvicorn. Detalhes em `docs/`.

## Comandos (terminal)

```bash
.venv/bin/python manage.py migrate
.venv/bin/python -m uvicorn config.asgi:application --host 127.0.0.1 --port 8000
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check chat config tests && .venv/bin/ruff format --check chat config tests
```

## Convenções

- Views finas; regras em `chat/services/`; SDK **só** em `services/`
  (`anthropic_client.py` constrói; `generation.py` orquestra).
- ORM em funções síncronas pequenas + `sync_to_async` no async. Sem
  `DJANGO_ALLOW_ASYNC_UNSAFE`. Sem `select_for_update` (SQLite; ADR-003).
- Uma execução ativa por conversa via UPDATE atômico; `database is locked`
  tem retry curto e vira `RunBusy`, nunca trava.
- Estados de run: preparing→streaming→done|failed|cancelled|interrupted|abandoned.
  Truncamento é flag. Tokens desconhecidos = null.
- Códigos de erro estáveis (`unauthorized`, `rate_limited`, …); sem `temperature`/
  thinking/beta por padrão.

## Invariantes (não quebrar)

- Payload reconstruído do SQLite: `system` no topo; `messages` só user/assistant;
  atual exatamente 1x; só resposta `ok` entra; redução remove turnos completos.
- Snapshot imutável por execução; `done` só após persistência final.
- Tudo filtrado por `owner`; UUID não autoriza. CSRF em mutações.
- Segredo nunca em HTML/JS/JSON/logs/SQLite (só referência Keychain).

## Proibições

- **Nunca** executar chamadas pagas automaticamente (só `test_live.py` com
  `CHAT_LIVE_TEST=1` explícito). **Nunca** expor/registrar segredos.
- Sem commit/push sem autorização. Sem Redis/Celery/Docker/banco vetorial.
