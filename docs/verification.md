# Verificação — evidências reais (2026-09-14, macOS ARM64, Python 3.13.5)

## Versões efetivamente testadas

Django==5.2.17, anthropic==1.5.0, uvicorn==0.53.0, asgiref==3.12.1,
python-dotenv==1.2.3, keyring==25.7.0, pytest==9.1.1, pytest-django==4.14.0,
pytest-asyncio==1.4.0, ruff==0.16.7 (`pip freeze`, sem `.env` no Git).

## Comandos executados e resultados

- `.venv/bin/python manage.py makemigrations chat` → `0001_initial` (5 modelos).
- `manage.py check` → no issues. `collectstatic` → 6 arquivos.
- `pytest tests -q --ignore=test_live` → **38 passed** (repetido, sem flake:
  unit, integração, serve uvicorn e navegador Chromium). `test_live.py` →
  skipped sem `CHAT_LIVE_TEST=1` (pago, opt-in).
- `ruff check` + `ruff format --check chat config tests` → limpos.
- Smoke uvicorn real (`config.asgi:application`, porta 8123): login→200,
  `/`→302 (login), CSS/JS→200, `/static/../manage.py`→404 (anti-traversal).
- `test_serve.py` sobe uvicorn de verdade e confere HTML+6 assets (critério 18).

## Mapeamento critérios → testes

1. `test_file_db (restart/recovery)`, `test_conversations`, `test_backup`
2. `test_cross_owner_blocked` (anon→302, outro owner→404, incl. export/runs)
3–6. `test_context_builder` (3 turnos, incompletos fora, orçamento, mínimo, count)
7. `test_catalog_diagnostics` (paginação `after_id`, invalidação, warn sem substituir)
8. `test_configuration` (precedência, sem fallback texto puro)
9–10. `test_deltas_before_done_and_persisted` (ordem de eventos + persistência)
11. `test_file_db` (abandoned × live), cancel cooperativo em `generation.py`
12. replay/conflito + `test_two_tabs_race_single_winner` (threads, SQLite arquivo)
13. `test_retry_reuses_question` (attempt 2, 1 pergunta)
14. `test_error_mapping…` (401/404/429/conexão + erro no meio do stream)
15. `test_truncation…` (flag + uso final substitui)
16. `test_security` (login, CSRF 403, XSS: JSON cru + templates escapam + sem
    `innerHTML` em código JS, chave nunca serializada, endpoint fora da allowlist)
17. `test_exports` + `test_backup_and_restore_roundtrip` (API backup + integrity)
18. `test_serve` + smoke curl acima.
19. RF-14 (`test_delivery` 4 + `test_response_mode` 19 + `sse.test.mjs` 6):
    modo completo sem `text_delta` na rede + `done` canônico com texto persistido;
    envelope `run_id`/`seq` em todos os eventos; delta na view antes do provedor
    concluir (gate `asyncio.Event`, sem sleep); heartbeat `: ping` sem cancelar o
    `__anext__` pendente; `cancelled` com upstream fechado + trava liberada;
    interrupção antes do 1º delta; `persist_failed`; parcial nunca vaza no modo
    completo; PATCH 409 durante geração; contexto/provedor idênticos nos dois modos.

## Limitações honestas

- Navegador real: `tests/e2e/test_browser.py` (Playwright + Chromium headless,
  `1 passed`, 4/4 estável) cobre login → chat → envio sem chave (erro exibido) →
  switch Streaming (rótulo, padrão ativado, teclado/Espaço, persistência via reload,
  rascunho intacto, mobile: header some e menu ⋯ assume) → configurações/diagnóstico,
  contra uvicorn real com banco e dotenv isolados (`CHAT_DB_PATH`, `CHAT_DOTENV_PATH`;
  nenhuma chave real, nenhuma rede). Streaming ponta a ponta no navegador exige chave
  e foi exercitado só via SDK simulado + smoke manual.
- Parser SSE em motor JS real: commitado em `tests/js/sse.test.mjs`
  (`node --test` → 6/6: evento partido entre chunks, UTF-8/emoji dividido,
  CRLF, vários eventos por chunk, comentários, JSON inválido isolado,
  markdown/código partido remontado).
- Durante o desenvolvimento do RF-14, um bug no helper de teste restaurou o
  `CLIENT_FACTORY` antes de consumir o stream e 2 execuções atingiram a API real
  (resposta curta em pt-BR, sem segredo envolvido). Corrigido com `monkeypatch`
  (restaura só no fim do teste) + trava de sessão em `tests/conftest.py`
  (`_block_real_provider`: `AsyncAnthropic` levanta fora de `CHAT_LIVE_TEST=1`).
  Suíte padrão desde então: 100% SDK simulado.
- Keychain: código usa `keyring`; sem validação contra Keychain real nesta sessão
  (sem prompt interativo executado).
- Teste pago (`test_live.py`) não executado (sem autorização de custo).
- Dev local com 1 worker; `.env` e `data/` fora do Git; usuário `local` de
  desenvolvimento criado para o smoke test — troque a senha ou recrie.
- `data/chat.sqlite3` e `.env` existentes são locais, não commitados.
