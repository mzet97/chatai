# Máquinas de estado M2

## Raiz (GenerationRun.state)

```text
queued → running → awaiting_approval → running → … → done | failed | cancelled
                     │                                  ↑
                     └─ cancel_requested(flag) ─────────┘
queued ← resume ← awaiting_approval | interrupted
running → interrupted (queda/erro recuperável; checkpoint explícito p/ resume)
```

Mapeamento com a especificação (§7): `preparing`/`streaming` legados são
sub-estados de `running` (preservados para não churnar o loop atual);
`cancel_requested` é flag cooperativa + estado visível `cancelled` ao
concluir; `interrupted_needs_review` (spec) = `interrupted` + checkpoint
para `resume` (sem reexecução automática de efeito ambíguo).
Terminais: `done`, `failed`, `cancelled`, `interrupted`, `abandoned`
(inalterados). `abandoned` = run substituída por retry (legado).

## Efeito de ferramenta (ToolInvocation.effect, legado preservado)

`unknown` até reconciliação após queda pós-envio; nunca reagendado por
expiração de lease. Retomada automática só de passo idempotente seguro.

## Claim (lite, SQLite)

`UPDATE … WHERE state='queued'` (compare-and-set, transação curta) +
`claimed_by`/`claimed_at`/`last_heartbeat`. Sem `SELECT … FOR UPDATE`
(SQLite não bloqueia linha). Fencing token fica para M3/PostgreSQL —
documentado, não simulado.
