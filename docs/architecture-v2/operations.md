# Operações (M4)

## Outbox de ingestão (§15)

Upload grava `IngestionJob` + `OutboxMessage` na mesma transação
(`chat/services/ingest/consumer.enqueue_ingest`, chamado em
`chat/views/api_rag.py`). O dispatcher (`manage.py dispatch_outbox --once`)
reivindica `pending` por CAS, publica com confirm no broker e só então marca
`delivered`. Confirmar entrega **não** comprova processamento: o consumidor
(`handle_ingest_requested`) é idempotente por job/versão e o job segue na fila
do `rag_worker` como fallback.

- Falha entre publish e marcação → `pending` + `attempts+1`; redispatch entrega;
  duplicata não duplica efeitos (teste `test_falha_entre_publish_e_marcacao_nao_perde_job`).
- `delivering` sem progresso volta à fila após 5 min (`updated_at`).
- Após 25 tentativas → `failed` (visível; sem retry infinito).
- Local-lite: `LocalBroker` (sem infra extra). Homelab: um `RabbitBroker` liga a
  mesma tabela; RabbitMQ fora do ar mantém jobs na outbox e a UI indica atraso —
  nunca descarta upload aceito (matriz de degradação).

## Índice de recuperação (§4, §14)

Fronteira `RetrievalIndex` (`chat/services/ingest/index.py`): `local` (FTS5
existente, oficial no local-lite) e `elastic` (projeção reconstruível do homelab;
sem `ELASTIC_URL`/pacote, falha com mensagem útil em vez de fingir consulta).
Perda do índice nunca apaga histórico/documentos: reindexa do banco.

## Comandos locais

- `manage.py rag_worker --once` — processa jobs da fila.
- `manage.py dispatch_outbox --once [--max N] [--no-consume]` — entrega outbox.
- `manage.py backup_db --out ...` / `restore_db` — backup/restauração SQLite.
- `manage.py preflight [--format json]` — checagem somente-leitura, sem segredos.

## Homelab (aterrissado em 16/09/2026, sem credenciais neste repo)

Fonte: `/Volumes/HD1TB/homelab-secure-edge-ansible` (+ `docs/CATALOGO-SERVICOS.md`).
Gerência: apps Ansible (`k8s/apps/<svc>`, tags) para rabbitmq/minio/authentik/
gateway; ECK/Elastic, Grafana/Prometheus/Loki via ArgoCD (repo gitops no Gitea).
Gateway API `homelab-gateway`, TLS `*.home.arpa` pela CA local (cert-manager).

| Serviço | Versão declarada | Endpoint (SNI) | Uso pela app |
|---|---|---|---|
| RabbitMQ | 4.3.5 | AMQP + management UI | broker do dispatcher (vhost/usuário/fila dedicados) |
| MinIO (S3) | 2025-09-07 | console + endpoint S3 | `ObjectStore` S3 (contrato substituível) |
| Authentik | 2026.8.2 | OIDC | login OIDC (vínculo issuer+sub) |
| Elasticsearch (ECK) | 9.5.3 | via GitOps | projeção `RetrievalIndex` |
| Redis | 8.10.1 | svc interno | aceleração descartável |
| PostgreSQL | externo ao k3s | host dedicado | banco oficial (base/usuário próprios, nunca os do Authentik) |

Alcançabilidade confirmada do Mac em 16/09/2026 (somente DNS+TCP+TLS,
sem login): `*.home.arpa` resolve para o nó k3s, 80/443 abertos, handshake
TLS completo nas rotas. Sem kubeconfig aqui: sem `kubectl`, sem deploy,
sem smoke além da borda TLS. Credenciais ficam no cofre/vault do homelab —
nunca neste repo, em logs ou em argumentos de ferramenta.
