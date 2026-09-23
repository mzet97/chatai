# Matriz de capacidades — SDK `anthropic==1.5.0` (tipos verificados)

Regra: só ID exato de modelo + metadados reais habilitam controles.
Ausente/nulo = desconhecido = controle desabilitado, preferência preservada.

## Suporte do SDK (tipado, sem upgrade necessário)

| Recurso | Tipo verificado |
|---|---|
| `thinking` enabled/disabled/adaptive | `ThinkingConfigParam`, `thinking_config_{enabled,disabled,adaptive}_param` |
| Manual `budget_tokens` (obrigatório) | `thinking_config_enabled_param: budget_tokens: Required[int]` |
| Níveis adaptativos | `OutputConfigParam.effort: low\|medium\|high\|xhigh\|max` (UI usa só low–high) |
| Exibição do resumo | `display: summarized\|omitted` em config adaptativa |
| Blocos de pensamento/opacos | `thinking_block`, `redacted_thinking_block`, `thinking_delta` |
| Imagem Base64 | `image_block_param`, `base64_image_source_param` (+ `url`, `file`) |

## Por modelo (a validar contra Models API da conta + docs)

| Modelo | Pensamento | Desligar | Visão | Temperatura |
|---|---|---|---|---|
| IDs exatos | adaptive \| legacy-budget \| always-on \| none \| **unknown** | sim \| não \| unknown | sim \| não \| unknown | suportada \| rejeitada \| unknown |
| Endpoint customizado | unknown (contrato não confirmável) | unknown | unknown | unknown (§2: `variation.py` já trata temperatura assim) |

Notas:
- Nunca copiar `thinking.type="enabled"` para todos; nunca inferir por nome.
- Conflito metadados × docs → bloqueia + registra, nunca o permissivo.
- Temperatura: `TEMPERATURE_SUPPORTED_MODELS` (≤ Opus 4.6) vs
  `TEMPERATURE_UNSUPPORTED_MODELS` (geração 5; Sonnet 5 verificado ao vivo
  400) já existem em `chat/services/variation.py` — reutilizar o padrão.
