# Research — versões e documentação consultada

Data da consulta: 2026-09-14. Ambiente: macOS ARM64 (M3), Python 3.13.5, VS Code.

## Versões fixadas (verificadas via PyPI em 2026-09-14)

| Pacote | Versão testada | Origem |
|---|---|---|
| Django | 5.2.17 (patch mais recente da série 5.2 LTS) | `pip index versions django` |
| anthropic (SDK oficial) | 1.5.0 | PyPI `anthropic/json` = 1.5.0 |
| uvicorn | 0.53.0 | instalado via pip |
| python-dotenv | 1.2.3 | instalado via pip |
| keyring | 25.7.0 | instalado via pip |
| pytest / pytest-django / pytest-asyncio | 9.1.1 / 4.14.0 / 1.4.0 | instalado via pip |
| playwright (+ Chromium headless shell) | 1.62.0 | instalado via pip; `python -m playwright install chromium` |
| ruff | 0.16.7 | instalado via pip |

Registro do ambiente (não garantia futura): `anthropic==1.5.0` testado pelo usuário com
`client.models.list()` e `base_url="https://api.anthropic.com"`; `claude-sonnet-5` presente.
O padrão inicial do app é `claude-sonnet-5`, sempre revalidado contra a Models API.

## Contratos do SDK verificados no código instalado (`anthropic==1.5.0`)

- `AsyncAnthropic(...).messages.stream(*, model, messages, system, max_tokens, timeout, ...)`
  retorna `AsyncMessageStreamManager` (método `stream` em `resources/messages/messages.py:2439`).
- Consumo: `async with client.messages.stream(...) as stream:`; deltas de texto via
  `stream.text_stream`; mensagem final via `await stream.get_final_message()`
  (`lib/streaming/_messages.py`: `AsyncMessageStream`, `get_final_message`, `request_id`
  lido do header `request-id`).
- Sem `temperature`/`top_p`/thinking/tools por padrão na v1 (parâmetros omitidos).
- Contagem: `await client.messages.count_tokens(model=..., messages=..., system=...)`
  (`resources/messages/messages.py:2671`).
- Catálogo: `client.models.list(after_id=...)` com paginação por cursor
  (`resources/models.py:240`); percorrer até esgotar `has_more`.
- Exceções (`_exceptions.py`): `AuthenticationError` (401), `PermissionDeniedError` (403),
  `NotFoundError` (404), `RateLimitError` (429), `APITimeoutError`, `APIConnectionError`,
  demais `APIStatusError`.

## Documentação consultada

- SDK Python: https://github.com/anthropics/anthropic-sdk-python
- Conversas (Messages API): https://platform.claude.com/docs/en/build-with-claude/working-with-messages
- Streaming: https://platform.claude.com/docs/en/build-with-claude/streaming
- Contagem de tokens: https://platform.claude.com/docs/en/build-with-claude/token-counting
- Listar modelos: https://platform.claude.com/docs/en/api/models/list
- Erros da API: https://platform.claude.com/docs/en/api/errors
- Django e versões: https://www.djangoproject.com/download/
- Django async: https://docs.djangoproject.com/en/5.2/topics/async/
- Django/SQLite: https://docs.djangoproject.com/en/5.2/ref/databases/
- CSRF: https://docs.djangoproject.com/en/5.2/howto/csrf/
- python-dotenv: https://bbc2.github.io/python-dotenv/
- keyring: https://keyring.readthedocs.io/en/latest/
- Backup SQLite: https://www.sqlite.org/backup.html
- CLAUDE.md: https://code.claude.com/docs/en/memory

## Decisões derivadas da pesquisa

1. Django 5.2 LTS: série com suporte estendido; views assíncronas (`async def`) suportadas
   nativamente; `StreamingHttpResponse` funciona sob ASGI.
2. SQLite + ORM + migrations; `select_for_update()` NÃO oferece bloqueio de linha no SQLite —
   exclusividade de geração via atualização atômica condicional (`UPDATE ... WHERE estado`).
3. Sem `DJANGO_ALLOW_ASYNC_UNSAFE`: ORM chamado em funções síncronas pequenas via
   `asgiref.sync.sync_to_async`.
4. Teste de navegador: Playwright exige download de browser; nesta entrega os fluxos críticos
   são cobertos por testes Django (Client + async) e um teste de fumaça do HTML/JS servido.
   Ver `docs/verification.md` para o motivo registrado.
