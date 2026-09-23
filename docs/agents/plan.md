# Plano M1–M5 — agentes, cache, pensamento (arquivos + testes)

## M1 — Perfis versionados + agente individual
- `chat/models_agents.py` (novo): `AgentDefinition`, `AgentVersion`
  (imutável), `AgentRun` (árvore, estado, tarefa, snapshot), `Delegation`,
  `BudgetLedger`, `CacheObservation`, `RunEvent`. Migration.
- `chat/services/agents/` (novo): `profiles.py` (CRUD/publicar/arquivar +
  exemplos idempotentes), `effective.py` (precedência + valores/origens).
- Rotas `api_agents.py` + página `agents.html` + `agents.js`; seletor
  Chat/Agente/Equipe na conversa.
- Testes `test_agents_m1.py`: cadastro, publicação, seleção, arquivamento,
  isolamento por dono, Chat sem regressão/delegação.

## M2 — CachePlanner + métricas
- `chat/services/cache.py` (novo): `CachePlanner` (desativado/estável/
  conversa, TTL, elegibilidade, diagnóstico) + aplicação no payload.
- `ModelStep`: `cache_creation_input_tokens`, `cache_read_input_tokens`
  (+ detalhe TTL); agregação por etapa/agente/raiz sem dupla contagem (fórmulas
  na docstring do adaptador).
- Testes `test_cache_m2.py`: modos, TTLs, payload idêntico com/sem cache,
  sem marcadores acumulados, sem padding, denominador zero.

## M3 — Pensamento por agente
- Reutiliza `chat/services/thinking.py`; config por versão de agente +
  override da conversa; replay separado pai/filhos.
- Testes `test_agent_thinking_m3.py`: adaptativo/legado/obrigatório/
  desconhecido, conflito com temperatura, pai × filho.

## M4 — Delegação + orçamento + recuperação
- Ferramenta interna `delegate_to_agent` (só coordenador/Equipe), filhos só
  leitura, cotas (2 filhas, profundidade 1, 10 gerações, 12 invocações,
  12k tokens, 180 s), ledger atômico, checkpoints, `RunEvent`.
- Testes `test_team_m4.py`: 0–2 filhos, recursão/3º filho bloqueados,
  paralelo com falha isolada, permissões, aprovação, cotas, reinício.

## M5 — UI final + avaliação + segurança
- Trilha da equipe, métricas, aviso de Equipe; RAG/imagens/MCP preservados;
  `docs/agents/eval.md` (≥20 tarefas, individual × equipe); revisão §18.
- Testes `test_agents_e2e.py` (navegador real) + README/CLAUDE.md.
