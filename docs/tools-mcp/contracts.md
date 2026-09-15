# Contratos

## Anthropic por etapa (loop)
- Requisição: `model, system, messages, max_tokens, tools=[{name, description,
  input_schema}], tool_choice={"type":"auto"}` (+ `extra_body.temperature`
  quando permitido, como hoje).
- Resposta: blocos `text | tool_use{id,name,input}`; `stop_reason ∈ {end_turn,
  tool_use, max_tokens, refusal, ...}`.
- Continuação: preserva a mensagem `assistant` integral; adiciona mensagem
  `user` com `[{type:"tool_result", tool_use_id, content, [is_error]}]`.
  Nunca `role="tool"`. `isError` MCP → `is_error`.

## SSE (estende o existente; todos com `run_id`+`seq`)
- `model_step_started{step}` · `tool_call_requested{invocation_id, tool, step}`
- `tool_approval_required{invocation_id, tool, origin, summary, args_preview}`
  (args completos validados, nunca delta parcial)
- `tool_started{invocation_id}` · `tool_finished{invocation_id, ok, truncated}`
- `run_paused{state:"awaiting_approval", pending:[invocation_ids]}` (terminal do
  segmento; retomar abre novo segmento SSE)
- `done` só no fim do turno inteiro, com texto final + uso agregado conhecido.

## Aprovação (POST, CSRF, idempotente)
- Req: `{approval_id, decision:"approve"|"deny", idempotency_key}`
- Resp: `{decision, invocation_id, consumed:true}` ou `409` (expirada/consumida)
  / `410` (política/catálogo mudou → nova decisão exigida).
- Texto no chat nunca aprova; `approved:true` em args é ignorado.

## MCPConnection (admin)
- `{alias, transport:"stdio"|"streamable_http", command?, args?, cwd?, url?,
  credential_ref?, scope_grants:[user_ids], enabled}` — sem segredos, sem shell.
- Teste de conexão retorna `{protocol_version, tools:[names], checked_at}` ou
  erro sanitizado; `active` só com `tools/list` válido.

## Limites (centralizados, `chat/services/tools/limits.py`)
`MAX_MODEL_STEPS=6`, `MAX_INVOCATIONS=8`, `TOOL_TIMEOUT_S=20`,
`ACTIVE_BUDGET_S=180` (fora espera humana), `MAX_TOOLS_EXPOSED=20`,
`MAX_RESULT_BYTES=32768`.
