# Inventário real (M0) — observado em 16/09/2026 no worktree

Natureza: evidência observada (arquivos, testes, comandos executados).
Nada aqui é herdado de SDD anterior sem verificação.

## Requisito → origem → estado observado → implementação → teste

| # | Requisito | Origem | Estado observado | Implementação | Teste |
|---|---|---|---|---|---|
| I-1 | Provedor Anthropic via SDK oficial | Mestre §2, Rev1.1 §0/§11 | IMPLEMENTADO | `anthropic==1.5.0`, `chat/services/anthropic_client.py` (`AsyncAnthropic`), `chat/services/generation.py` | suíte non-e2e (mocks), e2e navegador sem chave |
| I-2 | Ausência de dependência OpenAI | Rev1.1 §0 | VERIFICADO | `grep -rni openai` no código: zero ocorrências em `config/`, `chat/` (só menção em `docs/spec.md` negando compatibilidade); `requirements.txt` sem pacote OpenAI | transversal §24 ainda NÃO executado (SDK ausente + tráfego bloqueado) |
| I-3 | Chat/Agente/Equipe utilizáveis na UI | Mestre §3/§17 | IMPLEMENTADO | `chat/templates/chat/index.html` (seletor + `#team-banner`), `agent-select.js` (envios serializados), página `/agents/` | `tests/e2e/test_agents_browser.py` VERDE |
| I-4 | Perfis versionados imutáveis + exemplos idempotentes | Mestre §3/§4 | IMPLEMENTADO | `chat/models_agents.py`, `chat/services/agents/profiles.py`, `chat/views/api_agents.py` | `test_agents_api/profiles/m1` VERDES |
| I-5 | `delegate_to_agent` limitada (2 filhas, profundidade 1) | Mestre §5 | IMPLEMENTADO | `chat/services/agents/team.py`, `budget.py`, geração raiz/filhos em `generation.py` | `test_agents_m4_team.py` VERDE |
| I-6 | CachePlanner + métricas `usage` sem dupla contagem | Mestre §11–§14 | IMPLEMENTADO | `chat/services/agents/cache.py` (plan/apply/parse/aggregate) | `test_cache_m2.py` VERDE |
| I-7 | Pensamento por agente + replay separado | Mestre §9–§10 | IMPLEMENTADO | `chat/services/agents/thinking.py`, `thinking_stream/` | `test_agent_thinking_m3.py` VERDE |
| I-8 | RAG local (upload, FTS5, embeddings CPU) | Rev1.1 §16 | IMPLEMENTADO | `chat/services/rag/`, `sentence-transformers==6.0.1`, `transformers==5.17.0` | testes RAG VERDES |
| I-9 | Ferramentas locais/MCP + aprovações | Mestre §7 | IMPLEMENTADO | `chat/services/tools/`, `chat/models_tools.py` | testes tools/MCP VERDES |
| I-10 | Execução vinculada ao POST/stream da aba | Rev1.1 §1 (a mudar) | IMPLEMENTADO (legado) | `GenerationRun` + `active_run_id` na conversa; fechar aba = política de cancelamento atual | testes de cancel/retomada VERDES |
| I-11 | Banco SQLite, sem Postgres/S3/ES/broker | Rev1.1 §5 (local-lite) | IMPLEMENTADO | `config/settings.py:66` (`django.db.backends.sqlite3`, `CHAT_DB_PATH`) | suíte usa SQLite em arquivo |
| I-12 | Sem Redis/Celery/RabbitMQ/Elastic/OIDC/k8s | Rev1.1 | AUSENTE (conforme local-lite) | nenhum manifest, Dockerfile ou compose no repo | N/A |
| I-13 | Worker local persistente (RAG) | Rev1.1 §5 | IMPLEMENTADO | `chat/management/commands/rag_worker.py` | testes worker VERDES |
| I-14 | Execução durável separada de HTTP (runs/eventos/claims) | Rev1.1 §6–§8 (M2) | AUSENTE | sem `POST /api/.../runs`, sem `RunEvent` diário, sem claim/lease | N/A — primeiro gap |
| I-15 | Outbox + ingestão homelab | Rev1.1 §15 (M4) | AUSENTE | sem outbox, dispatcher, Celery | N/A |
| I-16 | Avaliação 20+ tarefas + comparativo individual×equipe | Mestre §20 | PARCIAL | `agents_eval.py`, `rag_eval.py`, docs/agents/eval.md; comparativo pareado NÃO executado | N/A |
| I-17 | Hit de cache real na API Anthropic | Mestre §20 | NÃO VERIFICADO | requer chave/saldo + autorização (proibido sem permissão) | N/A |

## Totais verificados nesta revisão

- `pytest tests/ --ignore=tests/e2e`: 422 passed, 1 skipped.
- `pytest tests/e2e/`: 4 passed (Chromium headless, sem chave/rede).
- `ruff check chat/`: limpo.
- Trabalho não commitado (113 arquivos no `git status`); sem commit/push por restrição.
