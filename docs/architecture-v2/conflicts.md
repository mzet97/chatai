# Conflitos resolvidos e dependências desconhecidas (M0)

## Conflitos entre SDDs anteriores e Rev1.1 — decisão

| # | Conflito | Decisão (Rev1.1) | Efeito no código atual |
|---|---|---|---|
| C-1 | Trechos antigos sugerindo outro provedor / "OpenAI-compatible" | Anthropic obrigatória e padrão; OpenAI é backlog opcional desativado | nenhum código OpenAI existe; nada a isolar. `docs/spec.md:47` já nega promessa "OpenAI-compatible" |
| C-2 | "Somente SQLite" vs PostgreSQL/S3 homelab | Dois perfis: local-lite (SQLite) + homelab (Postgres dedicado/S3) | sem mudança agora; contratos novos (ObjectStore, RunClaimStore) devem ter backend SQLite/local primeiro |
| C-3 | "Sem Redis/Celery" vs Redis/Celery/RabbitMQ no homelab | Mínimo local mantido; fila homelab só nas funções delimitadas (§15) | `rag_worker` local permanece; outbox desenhada para não exigir broker no lite |
| C-4 | "Fechar aba = interromper" vs execução durável | Muda em M2: fechar/recarregar só desconecta a visão; interromper exige ação explícita | comportamento atual (I-10) preservado até M2 entregar runs/eventos + UI de interrupção |
| C-5 | Agent SDK / gateways de terceiro | Proibidos como caminho; `AsyncAnthropic` direto | `anthropic_client.py` já conforme; sem LiteLLM/gateway |

## Dependências desconhecidas (marcadas, não presumidas)

| # | Incógnita | O que bloqueia | Tratamento |
|---|---|---|---|
| U-1 | Saúde/capacidade do cluster homelab, licença Elastic, permissão de criar banco PG | M3/M4/M6 | preflight somente-leitura em M6; nada afirmado até lá |
| U-2 | Distribuição MinIO instalada (repositório comunitário arquivado) | M3 storage | contrato S3 substituível; sem migração automática; limitar dados sensíveis até decisão operacional |
| U-3 | Modelos Anthropic disponíveis na conta do usuário + capacidades por modelo | M1/M5 | matriz por modelo exato + evidência; desconhecido = desabilitado (regra já usada em thinking/cache) |
| U-4 | Tabela de preços vigente (cache 5m/1h por modelo) | §14 estimativas | sem dados suficientes: exibir somente tokens |
| U-5 | Backend de traces (catálogo não comprova) | §22 | não afirmar traces distribuídos; ler etapas do banco + links de logs |

## Riscos herdados que continuam valendo

- Segurança de agentes/RAG (OWASP) — Mestre §18, já coberta em `docs/agents/threats.md`; revalidar nas rotas novas de runs.
- `.home.arpa` nunca público; Gateway API (sem Ingress); Ansible×ArgoCD sem gerenciar os mesmos objetos.
- Backup antigo nunca apaga conversas novas silenciosamente (rollback §23).
