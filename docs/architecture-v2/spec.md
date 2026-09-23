# Especificação mestre (M6)

Documento-índice: liga aos detalhes, não os duplica. Fonte normativa completa:
o PROMPT MESTRE SDD (goal da sessão) + referências [R0–R20]/[I1] nele citadas.

## Decisões vinculantes

- Anthropic obrigatória e padrão (`AI_PROVIDER=anthropic`); OpenAI é backlog
  futuro desativado (`OPENAI_ENABLED=false`), fora de M0–M6.
- Perfis: local-lite (SQLite, diretório privado, FTS5, worker local, login
  Django) e homelab (Postgres/S3/Elastic/RabbitMQ+Celery/OIDC) — mesma
  semântica de produto (`contracts.md`, `state-machines.md`).
- Quatro mudanças consolidadas: contratos Postgres/S3 sem migração automática;
  Redis/Celery só no homelab; execução persistida independente do HTTP;
  fechar aba ≠ interromper.

## Mapa dos documentos

| Documento | Conteúdo |
|---|---|
| `inventory.md` / `conflicts.md` | M0: requisito→arquivo/teste/estado |
| `architecture.md` | Visão do monólito modular e processos |
| `contracts.md` | Fronteiras: ModelProvider, ObjectStore, RetrievalIndex, Broker, OIDC |
| `state-machines.md` | Runs, jobs, outbox, aprovações |
| `threat-model.md` / `adr.md` | Ameaças (OWASP agentes/RAG) e decisões |
| `capabilities.md` | Matriz modelo×recurso (supported/unsupported/unknown) |
| `migration.md` | Procedimento + rollback |
| `operations.md` | Comandos, outbox, degradação |
| `evaluation-plan.md` | Evals comparativas + experimento de cache |
| `tasks.md` | Fatias M0–M6 e gates |
| `verification.md` | Ledger: verificado × não verificado |

Detalhe de agentes/cache/pensamento: `docs/agents/` (`spec.md`, `eval.md`).
