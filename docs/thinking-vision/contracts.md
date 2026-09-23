# Contratos de API e payload — Thinking + Visão (M1–M5)

Verificado no ambiente em 15/09/2026 (SDK `anthropic==1.5.0`, Django 5.2,
SQLite). Estado: pensamento — modelo, PATCH e resolvedor implementados;
fiação em `generation.py`, streaming e upload de imagem — planejados.

## C-1. Conversa — campos de pensamento (implementado)

Modelo `Conversation` (`chat/models.py:58-73`): `thinking_mode` (default
`default`), `thinking_level` (default `medium`), `thinking_budget` (default
1024), `thinking_show_summary` (default `False`). Migration sem tocar valores
existentes.

`PATCH /api/conversations/<uuid>` (`chat/views/api_conversations.py:136-165`):

- Aceita `thinking_mode` (`default|disabled|enabled`),
  `thinking_level` (`low|medium|high`), `thinking_budget` (`1024|2048|4096`),
  `thinking_show_summary` (bool). Valores fora do vocabulário → `400
  {"code": "validation", ...}`.
- Com `active_run_id` setado, qualquer desses campos → `409 {"code":
  "active_run", ...}` com eco do valor atual (mesmo padrão de
  `temperature_level`, `response_mode`).
- `temperature` numérico direto continua rejeitado com `400` (usar
  `temperature_level`).

`GET` detalhe / criação retornam eco + suportes
(`_conv_json`, `_thinking_support`, `_temperature_support`):

```json
{
  "thinking_mode": "default", "thinking_level": "medium",
  "thinking_budget": 1024, "thinking_show_summary": false,
  "thinking_support": {"capability": "unknown", "vision": "unknown", "model": "claude-sonnet-5"},
  "temperature_support": {"state": "unknown|supported|unsupported", "reason": "...", "model": "..."}
}
```

## C-2. Resolvedor — `resolve_thinking()` (implementado)

`chat/services/thinking.py:143-267`, coberto por
`tests/unit/test_thinking_caps.py`. Entrada: `(mode, level, model, base_url,
max_tokens, budget=1024, show_summary=False)`. Saída (dict):

| chave | tipo |
|---|---|
| `thinking` | dict ou `None` (omitir na chamada quando `None`) |
| `output_config` | dict ou `None` |
| `display` | eco informativo (`summarized\|omitted\|None`); só vale dentro de `thinking`, nunca como campo avulso |
| `state` | `default\|disabled\|adaptive\|legacy\|always_on\|none\|unknown` |
| `reason` | motivo legível (bloqueio/unknown) ou `""` |
| `conflict` | `None` ou `{"budget":…, "ceiling":…}` |

Regras (zero payload incompatível): `default` e `unknown` → ambas as chaves
`None`; `disabled` só envia `{"type": "disabled"}` quando a capacidade aceita
(nunca com `display`); `none`/`always_on` têm os retornos degradados
documentados no código; legado exige `1024 <= budget < max_tokens`, senão
`conflict` setado e chaves `None`.

## C-3. Payload do SDK (tipos verificados, `anthropic==1.5.0`)

- Adaptativo: `thinking={"type": "adaptive", "display": "summarized"|"omitted"}`
  + `output_config={"effort": "low"|"medium"|"high"}` (UI usa só low–high; sem
  `xhigh`/`max`). Nível → effort via `EFFORT_BY_LEVEL`.
- Manual: `thinking={"type": "enabled", "budget_tokens": 1024|2048|4096,
  "display": …}` com `budget_tokens < max_tokens`.
- Desligado: `thinking={"type": "disabled"}` (sem `display`, sem budget).
- Temperatura (modelos que aceitam, cf. `variation.py`): via `extra_body`
  passthrough — o SDK 1.5.0 removeu `temperature` dos métodos Messages.
  Pensamento e temperatura incompatíveis no mesmo turno: preferência preservada,
  marcada não aplicada e omitida.

## C-4. Fiação na geração (planejado M1, resto)

`chat/services/generation.py`: compor `thinking`/`output_config` centralmente
via `resolve_thinking()` + conflito budget×teto **antes** da chamada (oferecer
total confirmado); snapshot imutável por execução registra config
solicitada/efetiva (`think-map-v1`); mesma config em todas as etapas do turno
de ferramentas; `display` nunca como parâmetro avulso do Messages.

## C-5. Eventos de streaming (planejado M2)

`chat/services/thinking_stream.py` (novo): agregação por
execução/etapa/índice. Eventos da app —
`thinking_started|thinking_delta|thinking_completed` — só com projeções
liberadas. Protocolar integral e projeção sanitizada persistidos em campos
separados; replay com blocos completos (incl. opacos/`redacted_thinking` +
`signature`) e `tool_result` na ordem exigida. `signature`/opacos nunca na UI;
cópia da resposta copia só o texto final. "Pensando…" só com evento observado.

## C-6. Upload/análise de imagens (planejado M3–M4)

Rota multipart **separada** do RAG (`/api/rag/bases/<uuid>/upload` não é
reutilizado como pipeline — só o padrão cliente: FormData campo `file`, 1 POST
por arquivo, sem Base64 no navegador, cf. `upload.js`). `Pillow==12.3.0` já em
`requirements.txt` + venv.

```json
POST /api/conversations/<uuid>/attachments  (multipart, CSRF)
→ 201 {"attachment_uuid": "…", "state": "ready", "mime": "image/png",
       "width": 1280, "height": 720, "bytes": 412310, "position": 1}
```

Erros: `400` (formato/animação/dimensão/peso/MIME real — código
`validation`), `409` (limite 4/mensagem), `403` (conversa de outro dono),
respostas sem bytes do original. Limites: 4 novas/mensagem, 5 MiB e 20 MP por
original, 20 MiB serializados por chamada (incl. histórico). Formatos: JPEG,
PNG, WebP, GIF estático (animado rejeitado com explicação). Servidor valida
tudo com Pillow (MIME real, truncados, decompression bombs), grava variante
normalizada imutável (EXIF removido, sem metadados, sem ampliação/corte,
transparência preservada) + thumbnail; bloco `image` Base64 montado **só** na
serialização. Destino **Anexo da conversa**, nunca documento RAG indexado.
Modelo sem visão (`vision == "no"|"unknown"`) → geração bloqueada com oferta
(trocar modelo / remover anexos), sem descarte silencioso. Sem OCR como
substituto; sem Files API; texto em imagem/QR/nome é dado não confiável.
Previews/downloads por rotas autenticadas; abandonados expiram por rotina e
revogação invalida payloads/previews futuros.
