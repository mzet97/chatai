# Matriz de capacidades — agentes, cache, pensamento

Verificado no ambiente em 15/09/2026 (SDK `anthropic==1.5.0`, Django 5.2).
Regra geral: ausente/nulo = desconhecido = controle desabilitado,
preferência preservada.

## K-1. Modos de execução

| Modo | Perfil | Delegação | Filhas | Profundidade |
|---|---|---|---|---|
| Chat | — (conversa direta) | `delegate_to_agent` indisponível | 0 | 0 |
| Agente | 1 `AgentVersion` publicada | indisponível | 0 | 0 |
| Equipe | coordenador + especialistas | só coordenador | ≤ 2 | 1 (filho nunca delega) |

## K-2. Prompt caching real (SDK tipado, sem upgrade)

| Recurso | Tipo verificado |
|---|---|
| Breakpoint por bloco | `CacheControlEphemeralParam` = `{type: "ephemeral", ttl?: "5m"\|"1h"}` |
| Top-level (último bloco cacheável) | `MessageCreateParamsBase.cache_control` (`message_create_params.py:125-129`) |
| Blocos marcáveis | `Text/Image/Document/ToolUse/ToolResult/ToolParam` + correlatos |
| `system` com cache | só forma lista `Iterable[TextBlockParam]` (`:168`); string não carrega |
| Uso confirmado | `Usage.cache_creation_input_tokens`, `cache_read_input_tokens` (`types/usage.py`) |
| Detalhe por TTL | `CacheCreation` (`ephemeral_1h/5m_input_tokens`) — response-only |
| Stream | `RawMessageDeltaEvent.usage` (`MessageDeltaUsage`) + `message_start` |

NÃO existe (não inventar): `cache_creation` como parâmetro de request
(grep em `message_create_params.py` e params de bloco retorna vazio);
`MessageDeltaUsage` sem o objeto detalhado `cache_creation`.

## K-3. Pensamento por agente (reutiliza `thinking.py`)

| Capability (`capability_for`) | `default` | `disabled` | `enabled` |
|---|---|---|---|
| `adaptive` | omite | `{type:"disabled"}` | `adaptive` + `output_config.effort` (low/medium/high; sem `xhigh`/`max`) |
| `legacy` | omite | `{type:"disabled"}` | `enabled` + `budget_tokens` (1024/2048/4096, `budget < max_tokens` ou `thinking_conflict`) |
| `always_on` | omite | bloqueia c/ motivo | só `display` (`summarized` se resumo) |
| `none` | omite | omite | omite |
| `unknown` (tabelas vazias hoje) | omite | omite + motivo | omite + motivo |

Temperatura × pensamento: ativo → `temperature` omitida, preferência
preservada (`temperature_not_applied: thinking`, `generation.py`).
Resumo só do disponibilizado; replay pai/filho separados.
