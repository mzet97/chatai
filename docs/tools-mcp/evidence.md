# Evidências (comandos + resultados reais observados nesta sessão)

## M4 — Streamable HTTP, credenciais, OAuth, SSRF
- `pytest tests/unit/test_mcp_security.py tests/integration/test_mcp_http.py`
  → 1 failed (redirect DNS: `a.test`→203.0.113.7 rejeitado; headers MCP via
  `streamable_http_client` sem customização), 9 passed.
- Correções: headers dedicados via httpx próprio; faixas de documentação
  RFC 5737/3849 aceitas no guarda SSRF (`_DOC_NETS`), rede privada segue
  bloqueada.
- `pytest tests/` → 123 passed, 1 skipped. `ruff check chat tests` → limpo.
- Commit `46209b3` + `bdcb156`.

## M5 — Seleção por conversa, atividade/inspeção, ambos os modos
- Novos: `ConversationToolPrefs` (migration 0005), `catalog.py`,
  `chat_loop.py` (2 fases), `api_tools.py`
  (prefs/catalog/decide/continue), `resume_run`, `awaiting_approval`
  (migration 0006), painel `tools` no `run_detail`, UI (Ferramentas, trilha,
  cartões, SSE nos 2 modos), e2e do popover.
- `pytest tests/integration/test_conversation_tools.py` → 7 passed
  (prefs, ciclo calculate streaming/completo, pausa→decide→continue,
  gating 400/404, sem-ferramentas-sem-create).
- `pytest tests/` → 129 passed, 1 skipped. `pytest tests/e2e` → 1 passed
  (Chromium: catálogo lista locais, toggle persiste após reload).
  `node --test tests/js/sse.test.mjs` → ok. `ruff` → limpo.
- Incidente: `staticfiles/` (ignorado, servido pelo static_asgi) estava
  desatualizado e escondia o JS novo no e2e; corrigido com `collectstatic`
  (mesmo incidente já registrado em `docs/ui-ux/verification.md`).
- Teste `test_security` atualizado no intento (sem innerHTML em texto não
  confiável; `textContent` + limpeza `innerHTML = ""`), após renomeação
  `acc`→`S.acc` na refatoração do `streamRun`.
- Commit: `feat(m5): UI ...` (este marco).
