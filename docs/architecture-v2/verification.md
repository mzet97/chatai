# Verificação — ledger de evidências (M6)

Convenções: **verificado** = observado nesta máquina (comando/saída abaixo);
**não verificado** = exige credencial, cluster ou autorização inexistente aqui.
Documento não é prova; teste verde listado é a evidência.

## Suíte comum (verificado, 16/09/2026)

- `pytest tests/ --ignore=tests/e2e` → **453 passed, 1 skipped** (SQLite, mocks
  explícitos, sem chave/saldo/rede de IA).
- `NO_NET=1 pytest tests/e2e/` → 4 passed (fakes; navegador hermético quando
  aplicável).
- `ruff check chat tests` + `ruff format --check` → limpos.
- `manage.py agents_eval` → **24/24** (individual 8/8, equipe 16/16), sem OpenAI;
  relatório em `docs/agents/eval.md`.

## Por marco (verificado localmente)

| Marco | Evidência |
|---|---|
| M0 | `docs/architecture-v2/inventory.md`, `conflicts.md`, `threat-model.md`, `adr.md` |
| M1 | `tests/integration/test_provider_m1.py` (8) — Anthropic nativo, sem OpenAI |
| M2 | `tests/integration/test_runs_durable_m2.py` (7) — comandos/eventos/cancelamento |
| M3 | `test_object_store_m3.py` + `test_migration_rehearsal_m3.py` (8); `migration.md` |
| M4 | `tests/integration/test_outbox_m4.py` (5) — falha publish/marcação, duplicata |
| M5 | `test_agents_m5_evidence.py` (3) + 104 testes agentes/pensamento/cache/equipe; `evaluation-plan.md` |
| M6 local | `tests/integration/test_preflight_m6.py` (4); `manage.py preflight --format json`; `GET /api/health` |

## Parcialmente verificado — borda do homelab (16/09/2026)

Repo `homelab-secure-edge-ansible` inspecionado (somente leitura; segredos
não copiados): RabbitMQ 4.3.5, MinIO 2025-09-07, Authentik 2026.8.2, ECK
9.5.3, Redis 8.10.1, Gateway API `homelab-gateway`, TLS `*.home.arpa` via
cert-manager, PG externo dedicado. Do Mac: DNS `*.home.arpa` resolve,
TCP 80/443 abertos, handshake TLS completo — sem login, sem kubeconfig.

## Retomada M6 — preflight de cluster + manifests (22/09/2026)

- `manage.py preflight --cluster` (opt-in, `chat/services/cluster_preflight.py`:
  só `kubectl get|version`, nunca segredos): **10/10 ao vivo** — `cluster_api`
  server `v1.36.4+k3s1`, `cluster_nodes` 1 Ready sem MemoryPressure,
  `cluster_workloads` 5/5 prontos, `cluster_gateway` Programmed.
  (`anthropic_key` degraded = chave só no `.env`, não exportada no shell.)
- `deploy/` (namespace `chat-local` criado; resto só `dry-run=server`):
  `namespace/configmap/deployment/service/httproute.yaml` + `README.md`
  (segredos via `kubectl create secret`, imagem via Harbor, ordem de apply,
  smoke). Deployment + 3 objetos validados server-side; sem segredo no repo.
- Suíte: **463 passed, 2 skipped** (skips = testes pagos opt-in); ruff limpo
  nos arquivos tocados.
- Imagem pronta via SSH no nó 22/09/2026: `deploy/Dockerfile` (torch CPU,
  `psycopg[binary]`, `data/` recriado) → build+push
  `harbor.home.arpa/tese/chat-local:20260922-m6-2`; smoke no nó (SQLite):
  `migrate OK` + `/api/health` → `{"status":"ok","database":"ok"}`.
  (m6-1 descartada: faltava `data/` p/ o SQLite.)
- Pendente p/ fechar o M6: PG dedicado em 192.168.1.52 (base+usuário `chat_local`,
  exige senha admin do PG) + Secret (`deploy/README.md` §2) → `apply`
  autorizado → smoke/OIDC contra Authentik.

## Cluster ao vivo, somente leitura (22/09/2026, kubectl do Mac)

- Contexto `default` → `https://192.168.1.51:6443`; nó único `k8s1` Ready,
  k3s `v1.36.4+k3s1`, ~10Gi de memória alocável.
- Confirmado: Gateway `homelab-gateway` (Traefik, Programmed) + HTTPRoutes
  `.home.arpa`; Authentik, MinIO (100Gi), Kibana/Grafana no ar; namespaces
  `rabbitmq`, `redis`, `elastic`, `monitoring`, `argocd`, `harbor`, `gitea`.
- **Bloqueador**: nó sem memória — soma de requests ≈ 14,2Gi > ~10Gi
  alocáveis. Pendentes por `Insufficient memory`: `rabbitmq-0`,
  `elastic-es-default-0`, `redis-master-0`, `redis-replica-1`, `loki-0`,
  `gitea-valkey-cluster-1`. Metrics API indisponível.
- Consequência (§21): perfil homelab degradado — ingestão (RabbitMQ) e RAG
  Elastic indisponíveis até ação do operador; nenhum workload alheio foi
  alterado; nenhum manifest aplicado; deploy interno segue pendente.

## Verificado ao vivo (16/09/2026, chave do `.env`, custo autorizado)

- `CHAT_LIVE_TEST=1 pytest tests/integration/test_live.py` → **2/2 verdes**:
  auth (`models.list`), streaming + payload de contexto, e experimento
  escrita→leitura de cache com `cache_creation>0` / `cache_read>0` no `usage`.
- Achado: `claude-sonnet-5` pensa por padrão (ver `capabilities.md`).
- Observação (22/09/2026): reexecuções pagas em sequência rápida mostraram,
  de forma transitória, `cache_creation=0` com `cache_read>0` já na 1ª chamada
  de um prefixo com salt inédito — sem explicação confirmada no lado do
  provedor; experimento limpo sequencial no mesmo dia confirmou a semântica
  escrita→leitura (`created=8005` → `read=8005`), e o gate segue 2/2 verde.
  Hipótese local, não diagnóstico do servidor (§13).

## Não verificado (bloqueado por acesso/autorização)

- TTL 1h (exige espera real; fora da suíte por decisão, §20).
- SSH ao k3s (chave `homelab-secure-edge` fora deste Mac; `Permission denied`).
- `kubectl`/deploy, Postgres/S3/Elastic/RabbitMQ reais e concorrência PG
  (sem kubeconfig; borda TLS não prova saúde interna).
- OIDC contra Authentik, deploy GitOps, smoke/E2E no cluster, observabilidade
  (Prometheus/Grafana/Loki).
- Testes pagos de qualquer natureza.

## Matriz de degradação (§21) — estado

Comportamento com dependência ausente é testado onde dá localmente
(Elastic→erro útil, banco PG→erro útil, broker→outbox retém, provider sem
chave→estado explícito). Rolling update, queda real de serviço e restauração
no cluster: não verificados.
