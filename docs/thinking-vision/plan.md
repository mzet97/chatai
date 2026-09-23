# Plano M1–M5 (arquivos reais + testes por marco)

## M1 — Capacidades, prefs e validação
- `chat/services/thinking.py` (novo): resolvedor por ID exato espelhando
  `variation.py` (adaptive/legacy/always-on/none/unknown; visão sim/não/unknown).
- `Conversation`: `thinking_mode` (default `default`), `thinking_level`
  (default `medium`), `thinking_show_summary` (default False) — migration sem
  tocar valores existentes.
- `chat/services/generation.py`: compor `thinking`/`output_config`/`display`
  centralmente + conflito budget×teto antes da chamada; snapshot registra
  solicitada/efetiva; PATCH de conversa aceita os 3 campos (CSRF, bloqueio
  durante geração/aprovação).
- Testes `tests/unit/test_thinking_caps.py`: adaptive/legacy/always-on/sem
  suporte/desconhecido → zero payload incompatível; conflito 2.048×1.024.

## M2 — Resumo, streaming, replay
- `chat/services/thinking_stream.py` (novo): agregação por execução/etapa/
  índice; eventos `thinking_started|thinking_delta|thinking_completed` só com
  projeções; protocolar × projeção em campos separados.
- UI: seletor **Pensamento** + **Nível** + **Exibir resumo** (habilitados por
  `thinking_support`, como `temperature_support` em `chat.js`); painel
  recolhível **Resumo do pensamento**; "Variação" rotula o seletor atual.
- Replay preserva blocos completos + `tool_result` em ordem; registro
  versionado do contexto protocolar + aviso de reinício.
- Testes: resumo vazio/ausente/opaco/interrompido; round-trip com ferramentas.

## M3 — Upload visível e pixels reais
- `Pillow==12.3.0` já em `requirements.txt` e no venv (verificado 15/09/2026).
- `chat/static/chat/js/attach.js` (novo): Anexar + drag-drop + paste de imagem,
  miniaturas, Remover, zoom; reutiliza padrão de `upload.js` (FormData, 1 POST
  por arquivo, sem Base64 no navegador).
- Backend: `Attachment`/`ImageVariant` (owner, conversa, hash, MIME, dimensões,
  caminho privado, estado) + vínculo mensagem/anexo; endpoint multipart
  separado do RAG; validação Pillow + variante normalizada + thumbnail;
  montagem `image` Base64 só na serialização.
- Testes: UI (botão→anexo→estado), bytes corretos no payload do SDK, EXIF/
  transparência, MIME falso, truncado, animação, bomba, excesso, CSRF,
  acesso cruzado.

## M4 — Histórico, RAG/MCP, continuidade
- Reencontro pós-restart, variante imutável, reconstrução entre turnos,
  RAG textual junto das imagens, imagem em `tool_result` de ferramenta
  autorizada; bloqueio educado para modelo sem visão.
- Testes: continuidade, composição imagem+pensamento+RAG+ferramenta
  (stream on/off, aprovação pendente), revogação.

## M5 — Segurança, e2e, docs
- Prompt-injection visual, quotas, expiração de abandonados, revisão de saída,
  métricas; teste de navegador real (2 imagens, comparação, resumo, restart);
  README/CLAUDE.md (limites, retenção, visão≠OCR, effort≠temperatura).
- Marco de integração M4+M5 demonstra o fluxo final completo.
