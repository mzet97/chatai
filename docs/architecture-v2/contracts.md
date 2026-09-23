# Contratos M2 — comandos separados da execução

## Rotas

| Método + rota | Entrada | Resposta |
|---|---|---|
| `POST /api/conversations/{id}/runs` | `{text, idempotency_key, [model, thinking, tools…]}` (config gravada uma vez, nunca reinterpretada) | `202 {run_id, state}`; mesma chave + mesmo conteúdo → mesmo run (`202`, `created:false`); mesma chave + conteúdo diferente → `409 conflict` |
| `GET /api/runs/{id}` | — | snapshot autorizado (estende o painel atual; sem segredos) |
| `GET /api/runs/{id}/events?after=N` | cursor `after` (default 0) | SSE do diário confirmado: eventos `>after` com `seq`; `done` só após estado final persistido |
| `POST /api/runs/{id}/cancel` | — | `200 {cancelled, state}` idempotente (repetir não é erro) |
| `POST /api/approvals/{id}/decision` | `{decision: approve\|refuse}` (já existe) | inalterado; aprovação vinculada a operação/args/versão/usuário/execução |
| `POST /api/runs/{id}/resume` | — | `202` quando o estado permite (`awaiting_approval` resolvida, `interrupted`); senão `409` |

Regras: GET nunca gera/aprova/executa. Reabrir o stream recupera
eventos/estado (cursor), nunca repete o POST. Sem credencial na URL.
Apenas a raiz ocupa a exclusividade da conversa (`active_run`).

## Diário (RunEvent)

`{run, seq (única por run, monotônica), kind, payload (projeção liberada,
sem segredos/assinaturas), created_at}`. Texto em micro-lotes (limites de
tamanho/intervalo); nunca commit por token. Redis inexistente no lite:
polling limitado no banco é o mecanismo (não fallback).
