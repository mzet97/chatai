# Arquitetura — agentes, cache, pensamento

Modos: **Chat** (existente, sem delegação), **Agente** (perfil + loop),
**Equipe** (coordenador + até 2 especialistas, profundidade 1).
Regras duras: sem Agent SDK/CLI; delegação máx 2 filhas, profundidade 1;
sem memória global; cache real do provedor, nunca `lru_cache`.

```
Navegador (seletor Chat/Agente/Equipe, página Agentes, trilha, métricas)
  ↓ fetch + CSRF; SSE incremental
chat/views/api_agents.py (novo: CRUD, publicar, arquivar, exemplos)
chat/views/api_conversations.py (seletor de modo + override; 409 c/ active_run)
chat/services/agents/ (novo)
  profiles.py  (CRUD/publicar/arquivar + exemplos idempotentes)
  effective.py (precedência política → conversa → versão → padrões + origens)
  team.py      (delegate_to_agent: valida, cria AgentRun filho, agrega retorno)
  budget.py    (ledger atômico, cotas da árvore, cancelamento em cascata)
  cache.py     (CachePlanner: modo/TTL/elegibilidade + aplicação no payload)
  replay.py    (pensamento por agente; blocos pai/filho nunca se misturam)
chat/services/generation.py (reúso: reserve_run, snapshot, _emit, _finalize,
  cancel cooperativo; só a raiz ocupa Conversation.active_run)
chat/services/tools/chat_loop.py (reúso: LoopState fork, run_tool_events 2 fases)
chat/services/thinking.py (reúso: resolve_thinking; tabelas por ID exato)
chat/models_agents.py AgentDefinition/AgentVersion/AgentRun/Delegation/
  BudgetLedger/CacheObservation/RunEvent (implementado; ver contratos)
```

## Pontos de extensão verificados (reúso, não réplica)

- Reserva/idempotência: `generation.py` `reserve_run` + unique
  `(conversation, idempotency_key)` (`chat/models.py:133-174`). Filhos usam
  namespace próprio (`<run_pai>:<child_key>`) e sub-`run_uuid` próprio — o
  lock `Conversation.active_run` (`models.py:91`) é ocupado só pela raiz.
- Envelope: `_emit` = `run_id` + `seq` monotônica; `RunEvent(run, seq)`
  unique (`models_agents.py:187-189`) replica o padrão por AgentRun.
- Snapshot sem segredos: filho herda `conversation_uuid`, policy, thinking,
  delivery do snapshot do pai + `parent_run_uuid/agent_id/depth` no
  `ExecutionContext` (`tools/context.py:9-13`). Correção pendente:
  `executor.py:218-220` reconstrói o ctx sem `run_uuid` — perpetuar o campo.
- Tool path: `LoopState.to/from_snapshot` (`chat_loop.py:23-53`) é a
  primitiva de fork; unicidade `(run_uuid, step)` e
  `(conversation, run_uuid, tool_use_id)` (`models_tools.py:100-115,185-195`)
  exige `run_uuid` próprio por filho.
- Aprovações: `approvals.py` (`args_digest`, consumo único atômico, TTL
  10 min, `valid_for`) + `check_resume_entry` revalida tudo antes do efeito.
