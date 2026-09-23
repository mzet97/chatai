# Contratos — agentes, cache, pensamento

Modelos em `chat/models_agents.py` (implementado). Serviços em
`chat/services/agents/` (planejado, `plan.md` M1–M4).

## C-A1. Perfis — `AgentDefinition` + `AgentVersion`

- `AgentDefinition` (`models_agents.py:31-54`): `owner`, `name` (+ unique
  `owner+name`), `kind` (rótulo `general|researcher|reviewer|coordinator`),
  `description`, `archived`. Arquivar impede seleção, preserva proveniência.
- `AgentVersion` (`:57-87`): `definition`, `revision` (unique
  `definition+revision`), tarefa/modelo/tools/KBs/delegáveis, pensamento
  (`thinking_mode/level/budget`), `variation_level`, cache
  (`cache_mode` ∈ `disabled|stable|conversation`, `cache_ttl` ∈ `5m|1h`),
  `max_child_runs`, `published`. Publicada = imutável; edição gera nova
  revisão. Ação **Criar exemplos** idempotente (Geral/Pesquisador/Revisor/
  Coordenador), sem permissões automáticas; perfil incompatível = incompleto.
- Validadores: `thinking.normalize_*` (`thinking.py:74-92`); orçamento só
  `BUDGET_PRESETS (1024, 2048, 4096)`.

## C-A2. Config efetiva — precedência + origens

`effective.py`: política/tetos → override da conversa → versão do agente
→ padrões; subagente: tetos/escopo da raiz → versão do especialista.
Espelha `configuration.py:46-55` (`conversation→ui→env/file→default`),
retornando `(valor, origem)` por campo. Valores efetivos + origem
visíveis na UI; PATCH bloqueado com geração/aprovação pendente
(`api_conversations.py:123-156`, `409 active_run`).

## C-A3. Delegação — `delegate_to_agent` + `AgentRun`/`Delegation`

- Só o coordenador em modo Equipe; args: `especialista ∈ delegáveis`,
  `tarefa`, `critério`, `referências`. Sem código, prompt privilegiado,
  credencial, URL, permissões ou modelo nos args.
- `AgentRun` (`models_agents.py:90-121`): `parent` nulável, `depth`
  (0 raiz, 1 filho; neto = `409`), `task/completion_criteria`, `state`
  (`created|running|awaiting_approval|paused|done|failed|cancelled|
  budget_exhausted`), `result_summary/result_evidence/limitations/
  failure_reason`, `snapshot/checkpoint`. `Delegation` (`:124-135`):
  `parent_run`, `child_run` (1:1), `tool_use_id`, `released_context`.
- Retorno do filho (validado): `{estado, síntese, evidências, limitações,
  falha}`. Pai recebe produto + referências **revalidadas**; índices
  `search_result` não cruzam payloads; imagens só ao agente com visão.

## C-A4. Orçamento e trilha — `BudgetLedger` + `RunEvent`

- `BudgetLedger` (`models_agents.py:138-153`): `root_run` + `run`, `kind`
  (`reserve_output|usage|cache_write|cache_read|child_slot|call_slot`),
  `tokens`. Tetos da árvore: 10 gerações, 12 invocações, 2 filhas,
  profundidade 1, 2 simultâneas, 12.000 tokens saída, 180 s ativas.
  Reserva `max_tokens` antes de cada chamada; ambiguidade → margem
  conservadora. Cancelar a raiz fecha tudo (sem rollback).
- `RunEvent` (`:175-189`): `(run, seq)` unique; `kind+payload` para
  UI/recuperação. Checkpoints + retomada explícita sem repetir concluídas.

## C-A5. Cache observado — `CacheObservation` + `ModelStep`

- `CacheObservation` (`models_agents.py:156-172`): `run`, `step`, `mode`,
  `ttl`, `eligible`, `diagnosis`, `input_tokens`,
  `cache_creation_input_tokens`, `cache_read_input_tokens`
  (ausente = desconhecido, nunca zero presumido).
- Fórmulas (na docstring do adaptador): `entrada_total = sem_cache +
  gravada + lida`; `fração = lida / total` (zero protegido). UI separa
  solicitado, elegível, escrita/leitura confirmadas e sem confirmação.
  Só tokens — sem estimativa monetária.
- Payload: modo `stable` = breakpoint no fim das instruções aprovadas
  (tools determinísticas); `conversation` = `cache_control` top-level
  (SDK `message_create_params.py:125-129`, aplica ao último bloco
  cacheável); `system` em lista (`:168`) para carregar `cache_control`.
