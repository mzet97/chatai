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
  (`anthropic_client.py` constrói; `generation.py` orquestra; tools em
  `services/tools/` com loop explícito em `chat_loop.py`, nunca runner auto).
- ORM em funções síncronas pequenas + `sync_to_async` no async. Sem
  `DJANGO_ALLOW_ASYNC_UNSAFE`. Sem `select_for_update` (SQLite; ADR-003).
- Uma execução ativa por conversa via UPDATE atômico; `database is locked`
  tem retry curto e vira `RunBusy`, nunca trava. Pausa mantém `active_run`.
- Estados de run: preparing→streaming→awaiting_approval→streaming→done|
  failed|cancelled|interrupted|abandoned. Truncamento é flag. Tokens
  desconhecidos = null.
- Tools: prefs por conversa (default vazio); catálogo = interseção
  prefs∩disponível, congelado no snapshot; 2 fases (validar+abrir
  aprovações, depois executar); resume revalida tudo antes do efeito.
- Códigos de erro estáveis (`unauthorized`, `rate_limited`, …); sem `temperature`/
  thinking/beta por padrão.

## Invariantes (não quebrar)

- Payload reconstruído do SQLite: `system` no topo; `messages` só user/assistant;
  atual exatamente 1x; só resposta `ok` entra; redução remove turnos completos.
  Blocos `tool_use`/`tool_result` vivem no loop, nunca no histórico.
- Snapshot imutável por execução (+`tools_enabled` e `tool_loop` na pausa);
  `done` só após persistência final.
- Tudo filtrado por `owner`; UUID não autoriza. CSRF em mutações.
  Aprovação só pelo endpoint decide (uso único); texto no chat nunca aprova.
- Segredo nunca em HTML/JS/JSON/logs/SQLite (só referência Keychain).
  Nomes/descrições/resultados MCP são texto não confiável (textContent).
- Imagens (M5): upload stateless `POST /api/images`; envio revalida e
  persiste em `Message.blocks`; Base64 só na serialização ao provedor —
  nunca em listagem (`public_image`), exportação, logs ou snapshots.
  Limites 4/msg, 5 MiB/20 MP/arquivo, 20 MiB serializados; quotas por
  conversa 40 imagens/80 MiB (`429 quota`). Nome de arquivo é dado não
  confiável: DLP recusa padrão de segredo sem eco (`check_filename_safety`),
  resto sanitizado; texto/QR em pixel é dado, nunca ordem. Visão ≠ OCR;
  effort (pensamento) ≠ temperatura. Abandonadas expiram no startup +
  `manage.py expire_runs`, sem tocar ativas.

## Proibições

- **Nunca** executar chamadas pagas automaticamente (só `test_live.py` com
  `CHAT_LIVE_TEST=1` explícito). **Nunca** expor/registrar segredos.
- Sem commit/push sem autorização. Sem Redis/Celery/Docker/banco vetorial.

## Agentes (M1–M5)

- Perfis em `chat/models_agents.py` + `chat/services/agents/` (`profiles.py`,
  `effective.py`, `cache.py`, `thinking.py`, `team.py`, `budget.py`,
  `eval.py`). Página `chat/templates/chat/agents.html` + `agents.js`;
  seletor em `agent-select.js` (independente do `chat.js`); aviso de Equipe
  (`#team-banner`); trilha/métricas no inspetor (`renderAgentTrail`) a partir
  de `api_runs.run_detail["team"]` + `tools.steps` (cache) — sem segredos.
- Precedência C-A2: conversa → versão publicada → padrões. Chat = `None`
  (perfil pré-escolhido fica inerte). Equipe exige coordenador + delegáveis;
  sem delegáveis, responde só (`no_delegatables`, sem `delegate_to_agent`).
- Filhos: só leitura, profundidade 1, máx 2, sem `delegate_to_agent` no
  catálogo (`child_catalog`); resultado validado vira dado (`validate_child_result`).
  Cache real do provedor, nunca `lru_cache`; ausente = desconhecido.
- Suite sintética: `python manage.py agents_eval` → `docs/agents/eval.md`
  (24 tarefas, individual × equipe). E2E real: `tests/e2e/test_agents_browser.py`.
