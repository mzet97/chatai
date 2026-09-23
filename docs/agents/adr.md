# ADRs — agentes, cache, pensamento

Contexto em `spec.md`; arquitetura em `architecture.md`; contratos em
`contracts.md`; execução em `plan.md`.

## ADR-A1 — Client SDK (`AsyncAnthropic` + Messages API), sem Agent SDK/CLI

Decisão: orquestração própria sobre o Client SDK instalado
(`anthropic==1.5.0`, `pyproject.toml`); nenhum Agent SDK, runner
automático ou CLI de agente. Motivos: (1) aprovação humana com pausa e
retomada exige loop explícito em `chat_loop.py` (`run_tool_events`,
2 fases) — runners escondem esse ponto de controle (precedente ADR-T1 em
`docs/tools-mcp/architecture.md`); (2) convenção do repo — SDK só em
`chat/services/` (`anthropic_client.py` constrói, `generation.py`
orquestra; `CLAUDE.md`); (3) snapshot imutável/auditoria por execução.
Consequência: `delegate_to_agent` é ferramenta interna nossa, não
primitiva de framework.

## ADR-A2 — Sem memória global entre agentes/conversas

Cada `AgentRun` carrega só: snapshot do pai (proveniência), tarefa e
contexto liberado em `Delegation.released_context`. Nada de store
compartilhado, nada de histórico cruzado; `owner` filtra tudo (invariante
`CLAUDE.md`). Citação do filho nunca é prova no pai (revalidação §C-A3).

## ADR-A3 — Cache real do provedor; nunca `lru_cache` para prompt caching

Prompt caching = breakpoints `cache_control: {type: "ephemeral",
ttl: "5m"|"1h"}` (`CacheControlEphemeralParam`, SDK) + leitura de
`Usage.cache_creation/cache_read_input_tokens` (`types/usage.py`).
`functools.lru_cache` é proibido nesse caminho: esconderia custo/latência
reais e fingiria economia sem confirmação do provedor. Nota: o único
`lru_cache` da base (`base_policy.py:33`, arquivo de política local com
`cache_clear()` explícito) não é prompt caching e não conta como cache
de inferência; o `ModelCatalogCache` (`model_catalog.py`) é cache de
metadados com invalidação, não de prompt.

## ADR-A4 — Filhos só leitura; consentimento vira limitação

Filho executa só ferramentas de leitura do catálogo autorizado; escrita
exige aprovação que o filho não pode obter — retorna `consentimento
necessário` como limitação no objeto de retorno, e o pai decide.
Efeito `unknown` nunca tem retry automático.

## ADR-A5 — Desconhecido = desabilitado (pensamento e cache)

Modelo fora das tabelas de `thinking.py` (`CAP_*` vazios,
`capability_for`/`vision_for`) ou prefixo de cache abaixo do limiar de
elegibilidade → controle omitido, preferência preservada, diagnóstico
registrado. Nunca inferir por nome de modelo; nunca padding para forçar
elegibilidade; nunca dois TTLs/estratégias por chamada; nunca marcar
bloco `thinking`.
