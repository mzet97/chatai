# Arquitetura — claude-chat-local

## Componentes

```text
navegador (templates + chat/static/chat/js/*.js)
  │  HTML inicial (SSR) + fetch JSON/SSE + CSRF
  ▼
views (finas: auth, páginas, API JSON, SSE)
  │  chamadas
  ▼
services/
  configuration.py  leitura/precedência, sem expor segredo
  anthropic_client.py  construção do AsyncAnthropic isolada (único ponto de import do SDK)
  model_catalog.py  Models API paginada + cache
  context_builder.py  reconstrução determinística + orçamento + count_tokens
  generation.py  orquestração da execução (estados, idempotência, persistência)
  exports.py  JSON versionado + Markdown, sem segredos
  ▼
models.py (Conversation, Message, GenerationRun, ConnectionSettings, ModelCatalogCache)
  ▼ SQLite data/chat.sqlite3 via ORM + migrations
```

Regra: chamadas ao provedor só em `services/`; nunca em templates, models ou import
de módulos.

## Dados e estados

Conversation: uuid, owner, title, archived, preferred_model, system_prompt, params
(max_output_tokens, input_budget, strict_mode), created_at/updated_at.
Message: uuid, conversation, seq (único por conversa), role (user/assistant),
text, state (`ok`, `partial`, `failed`, `cancelled`), created_at.
GenerationRun: uuid, conversation, user_message, assistant_message (nullable),
attempt, idempotency_key (única por conversa), content_hash, state
(`preparing` → `streaming` → `done` | `failed` | `cancelled` | `interrupted` |
`abandoned`), snapshot (JSON não secreto), requested_model/actual_model,
request_id/response_id, input/output tokens (null = desconhecido), stop_reason,
truncated flag, error (sanitizado), tempos.
ConnectionSettings: owner (único), opções não secretas, keychain_ref, origem, revision.
ModelCatalogCache: profile/endpoint, payload JSON, fetched_at.

Transições: `preparing`→`streaming`→terminal. `interrupted` = cliente desconectou;
`cancelled` = usuário interrompeu; `abandoned` = heartbeat perdido (recuperado no
startup por `last_heartbeat` antigo sem dono vivo). Truncamento é flag, não estado.

## Configuração (precedência)

Não secretas: conversa → preferência interface → ambiente → `.env` → padrão.
Credencial: Keychain selecionada → ambiente → `.env`. Leitura com `dotenv_values`
da raiz do projeto; sem mutar `os.environ` por requisição. Endpoint allowlist:
`ANTHROPIC_BASE_URL` deve ter host na lista confiável do servidor e HTTPS
(exceto `127.0.0.1` para testes locais).

## Streaming (contrato HTTP/SSE)

`POST /api/conversations/<uuid>/messages` `{content, idempotency_key}` → `201`
`{run_id, user_message_id}` + cabeçalho `Location` do stream, ou `200` com estado
existente (replay idempotente), ou `409` (conflito de hash).
`GET /api/runs/<uuid>/stream` (SSE, mesma sessão/CSRF de leitura GET):
`run_started {run_id, model}` → `text_delta {text}`* → `usage {input, output}` →
`done {message_id, stop_reason, truncated}` | `error {code, message}`.
Códigos de erro estáveis: `unauthorized`, `forbidden`, `not_found`, `model_unavailable`,
`rate_limited`, `api_timeout`, `api_connection`, `api_error`, `context_too_large`,
`count_failed`, `conflict`, `run_busy`, `validation`, `persist_failed`, `cancelled`,
`interrupted`.

## Concorrência

Exclusividade por conversa: `Conversation.objects.filter(uuid=..., active_run_id=None
ou run terminal).update(active_run=...)` atômico; se 0 linhas → `409 run_busy`.
Liberação em `finally` da finalização. Checkpoints de parcial a cada ~2s ou 4KB
(nunca por token). Timeout SQLite curto; tratar `database is locked` com retry
limitado em escrita de checkpoint.

## Segurança

Login obrigatório em tudo; filtro `owner=request.user` em todas as queries;
UUID não autoriza. CSRF em POST/PUT/PATCH/DELETE. Chave só no backend, nunca
serializada. Logs sem corpo de conversa.
