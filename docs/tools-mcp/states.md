# Estados

## GenerationRun (estende o existente)
`preparing → streaming → awaiting_approval → streaming → done`
`awaiting_approval` = segmento HTTP encerrado, `run_paused` emitido, nada em
memória esperando. Retomada volta a `streaming`. Terminais anteriores mantidos.

## ToolInvocation
`requested → awaiting_approval → approved → running → done`
`requested → awaiting_approval → denied → done(refused)`
`requested → invalid/unknown/blocked → done(error)` (sem executar)
`running → done | failed | unknown` (queda pós-envio sem confirmação)
Cancelamento do turno não regride invocação `done`; só impede novas etapas.

## ToolApproval
`pending → consumed | expired | superseded`
Consumo atômico (`UPDATE ... WHERE state='pending' AND expires_at>now`);
duplo clique = 1 efeito. Mudança de args/schema/revisão/política exige nova linha.

## MCPConnection
`disabled → testing → active | error`; `active → suspended` (catálogo mudou ou
revogada) → `active` após revisão. `connected` só com `tools/list` válido.
