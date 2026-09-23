# Arquitetura (M6)

Monólito modular Django (ASGI), um modelo de domínio, uma linha de releases.
Separação de processos ≠ microserviços.

```text
Navegador (Templates + JS/CSS modular, SSE com cursor)
  │ HTTPS, sessão, uploads, comandos, observação
Django ASGI — UI, identidade (`services/identity.py`), autorização, API
  ├── SQLite (lite) / PostgreSQL dedicado (homelab) — estado oficial
  ├── Diretório privado (lite) / S3 (homelab) via `ObjectStore`
  ├── FTS5+NumPy (lite) / Elastic projeção (homelab) via `RetrievalIndex`
  └── Outbox (`OutboxMessage`) → `dispatch_outbox` → broker local (lite) /
      RabbitMQ+Celery (homelab) → `rag_worker`
Runtime LLM: worker local único (lite) / asyncio com claim-lease (homelab)
  ├── `AnthropicProvider` (SDK oficial, AsyncAnthropic) — único provedor
  ├── agentes: perfis versionados, `delegate_to_agent` (≤2 filhas, depth 1),
  │   orçamento global (`BudgetLedger`), pensamento e cache por agente
  └── ferramentas locais/MCP aprovadas; RAG com citações revalidadas
```

## Módulos e fronteiras

`identity` · `conversations` · `runtime` · `agents` · `providers` ·
`knowledge` · `tools` · `assets` · `evaluations` · `operations`
(organização lógica sobre o app `chat`; ver `contracts.md`).

Fronteiras com duas implementações: `ModelProvider`, `ObjectStore`,
`RetrievalIndex`, `Broker`, OIDC (`identity.py`). Sem repositories genéricos.

## Invariantes

- Views não coordenam loops LLM; templates não chamam provedores; agentes não
  escrevem em bancos/S3/Elastic — pedem ferramentas autorizadas.
- Transações curtas, CAS no SQLite (nunca `select_for_update` como lock real).
- Redis = aceleração descartável; Elastic = projeção reconstruível.
- Falha de dependência → degradação explícita (§21), nunca sucesso fictício.
