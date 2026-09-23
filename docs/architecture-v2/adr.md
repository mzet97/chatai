# ADRs — decisões vinculantes (Rev1.1, 2026-09-16)

Formato: contexto → decisão → consequência. Revisões exigem nova entrada,
nunca reescrita.

## ADR-1 Monólito modular, processos por responsabilidade

Separação em processos (web ASGI, `run_worker`, `rag_worker`,
dispatcher futuro), um só modelo de domínio e uma linha de releases.
Rejeitado: microserviço por agente, bancos/contratos duplicados.

## ADR-2 Execução separada de HTTP

Comando (`POST …/runs`, 202) ≠ observação (`GET events` com cursor).
`execute_run` é o único executor, chamado pela view legada ou pelo
worker. Fechar a aba desconecta a visão; só `cancel` interrompe.

## ADR-3 Claim/lease/fencing e efeitos desconhecidos

Lite: compare-and-set + `claimed_by` + heartbeat (sem `select_for_update`
no SQLite). Homelab: `FOR UPDATE SKIP LOCKED` + fencing token (M3).
Queda pós-envio = efeito `unknown`; sem reagendamento por expiração.

## ADR-4 Outbox (M4)

Upload/solicitação grava job + outbox no banco; dispatcher publica com
confirm e só então marca; consumidor idempotente por job/versão/operação.
Lite continua sem broker.

## ADR-5 Dois perfis, uma semântica

local-lite (SQLite, arquivos, FTS5, worker local, login Django) e homelab
(Postgres dedicado, S3, Elastic, Celery/RabbitMQ, OIDC). Sem matriz
combinatória; sem Docker obrigatório no lite.

## ADR-6 Postgres/S3/Elastic por responsabilidade (M3/M4)

Postgres = estado oficial; S3 = binários imutáveis com SHA-256 próprio
(ETag não substitui); Elastic = projeção reconstruível, nunca autorização;
Redis = descartável. Sem transação única entre os três.

## ADR-7 Runtime próprio limitado vs SDKs gerenciados

`AsyncAnthropic` direto + `ModelProvider` mínimo. Rejeitados: Agent SDK
(outra abstração, runtime Claude Code), LangChain/LangGraph/CrewAI,
gateway OpenAI-compatible como caminho Claude.

## ADR-8 Segredo/retenção

`ANTHROPIC_API_KEY` em cofre/.env/Keychain; UI nunca lê segredo de volta;
OAuth mutável cifrado com chave fora do banco (ou indisponível com sinal
claro). Cache/retenção para o provedor exige permissão; remover
`cache_control` não promete ausência de cache interno.

## ADR-9 Isolamento MCP

Descoberta ≠ ativação; annotations são alegações. No cluster: sem stdio
arbitrário, sem terceiros no pod com chaves, OAuth/Bearer ≠ OIDC, SSRF
inclui discovery OAuth (allowlist específica).

## ADR-10 Contrato de provedores

`get_provider()` resolve `anthropic` e levanta `ProviderUnavailable` para
o resto — estado explícito, nunca fallback. `AI_PROVIDER=anthropic`,
`OPENAI_ENABLED=false` (trava documental). Capacidades por ID exato;
`unknown` = desabilitado.

## ADR-11 Cache vs replay

Fenômenos distintos: perda de reaproveitamento ≠ invalidade de bloco
preservado. Marcadores de cache não são conteúdo semântico; assinaturas
não são transferíveis entre agentes/provedores. Fórmula de uso no
adaptador, sem dupla contagem.

## ADR-12 Migração/rollback

Fases com ensaio em destino separado; expand/contract; rollback simples
só pré-corte; pós-corte = export/reconciliação ou perda aceita. Sem
big-bang, sem sync bidirecional, sem migração automática de storage.
