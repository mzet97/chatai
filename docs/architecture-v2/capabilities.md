# Matriz de capacidades (M1) — SDK `anthropic==1.5.0`

Regra: capacidade por **ID exato de modelo + endpoint/perfil**, com
evidência. Estados: `supported` / `unsupported` / `unknown`.
Desconhecido = desabilitado (nunca inferido pelo nome, ordem ou lista).

## Tabelas (código)

| Recurso | Tabela | Estado atual |
|---|---|---|
| Pensamento adaptativo/legado/always_on/none | `thinking.CAP_*_MODELS` (+ `capability_for`) | vazias → tudo `unknown` |
| Visão yes/no | `thinking.VISION_*_MODELS` (+ `vision_for`) | vazias → tudo `unknown` |
| Cache 5m/1h | `agents/cache.py` (modos/TTL por perfil) | parametrizado; hit só via `usage` |

Endpoint fora de `api.anthropic.com` sem evidência própria → `unknown`
para pensamento e visão (regra em `capability_for`/`vision_for`).

## Modelos confirmados (chamadas reais autorizadas, 16/09/2026, SDK 1.5.0)

| Modelo | Pensamento padrão | `disabled` | Cache 5m (write→read) |
|---|---|---|---|
| `claude-sonnet-5` | **pensa** (bloco `thinking` consome `max_tokens` pequeno; `stop=max_tokens`, texto vazio) | `supported` (texto direto, `end_turn`) | `supported` (`cache_creation>0` → `cache_read>0` no `usage`) |

Evidência: `tests/integration/test_live.py` (opt-in `CHAT_LIVE_TEST=1`, 2/2
verdes em 16/09/2026). Consequência: nunca assumir "padrão = desligado";
`max_tokens` pequeno sem `thinking` explícito pode retornar só pensamento.

## Como confirmar um modelo (evidência exigida)

1. Resposta real da Models API (`id` exato) ou erro 400 documentado.
2. Para pensamento: chamada real com `thinking`/`output_config.effort`
   (autorizada, fora da suíte comum) ou rejeição explícita do provedor.
3. Para visão: resposta com bloco imagem aceito vs rejeitado.
4. Registrar aqui: modelo, endpoint, revisão do SDK, data, resultado.

Modelos fora da tabela acima permanecem `unknown`; a UI apresenta os
controles como não confirmados nesse caso.
