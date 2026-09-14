# Tarefas rastreáveis

Formato: objetivo · requisitos · arquivos · teste · conclusão.

## B — Base
- B1 Projeto Django+ASGI executável · RNF-01 · `config/`, `manage.py`, `pyproject.toml` ·
  `tests/integration/test_serve.py` (HTML+assets via ASGI) · `uvicorn` serve 200.
- B2 Login + bootstrap local sem senha padrão · RNF-01 · `chat/views/accounts.py`,
  `chat/management/commands/create_local_user.py` · `test_auth.py` (login exigido,
  UUID não autoriza) · login funcional, sem senha embutida.
- B3 CRUD conversas/mensagens + busca paginada + arquivar/excluir · RF-02 ·
  `chat/models.py`, `chat/views/*.py` · `test_conversations.py` (persistência,
  isolamento por owner, restart) · CRUD verde.
- B4 Export JSON/Markdown sem segredos · RF-02 · `chat/services/exports.py` ·
  `test_exports.py` · export sem chave/config secreta.

## C — Configuração e catálogo
- C1 Precedência + `.env` raiz + origem efetiva · RF-04 · `services/configuration.py` ·
  `test_configuration.py` · precedência correta, segredo nunca serializado.
- C2 Keychain via keyring, sem fallback texto puro · RF-05 · `services/configuration.py`,
  tela config · `test_configuration.py` · indisponível mantém `.env`, sem SQLite puro.
- C3 Diagnóstico 4 passos + erros diferenciados · RF-06 · `services/diagnostics.py`,
  view · `test_diagnostics.py` (401/403/429/timeout/conexão) · passos distinguíveis.
- C4 Catálogo paginado + cache + invalidação · RF-07 · `services/model_catalog.py` ·
  `test_model_catalog.py` (paginação, invalidação, preservação em falha) · catálogo real.

## D — Contexto e geração
- D1 Reconstrução 3 turnos + system separado · RF-08 · `services/context_builder.py` ·
  `test_context_builder.py` · ordem correta, atual 1x, incompletos fora.
- D2 Orçamento + redução por turnos + estrito + count_failed · RF-09 · idem ·
  `test_context_budget.py` · só turnos completos removidos, banco intacto.
- D3 Geração textual persistida + snapshot + painel · RF-03/10/11 · `services/generation.py` ·
  `test_generation.py` · resposta persiste antes de concluir; modelo real registrado.

## E — Streaming e robustez
- E1 SSE deltas antes de done, UTF-8 fragmentado · RF-11 · view stream + `static/.../stream.js` ·
  `test_streaming.py` · delta precede done; parser tolera fragmentação.
- E2 Cancel/interrupt/abandon, sem parcial→sucesso · RF-12 · `generation.py` ·
  `test_interruption.py` · estados corretos após cada evento.
- E3 Idempotência + 1 ativa por conversa + retry explícito · RF-13 · `generation.py` ·
  `test_idempotency.py` (duplo clique, 2 abas, SQLite arquivo) · sem chamada duplicada.
- E4 Erros 401/403/404/429/timeout/meio-stream + truncamento · RF-06/11/12 ·
  `generation.py` · `test_api_errors.py` · códigos estáveis, sem diagnóstico inventado.
- E5 Modo de entrega por conversa (switch Streaming) · RF-14 · `services/delivery.py`,
  `generation.py`, `api_runs.py`, `api_conversations.py`, `chat.js` ·
  `test_delivery.py` (validação/resolução) + `test_response_mode.py` (19 testes:
  PATCH persiste/409/isolamento, completo sem deltas + done canônico, envelope
  run_id/seq, contexto idêntico nos dois modos, incremental na view com gate,
  heartbeat sem matar a geração, cancelled/interrupted/persist_failed, sem vazamento
  de parcial, idempotência) + `tests/js/sse.test.mjs` (`node --test`: fragmentação,
  UTF-8 dividido, CRLF, markdown partido) + switch no `test_browser.py` · uma chamada
  de provedor por tentativa; entrega ≠ geração.

## F — UI e dados
- F1 UI pt-BR, Markdown seguro, XSS bloqueado · RF-01 · templates/static ·
  `test_security.py` (XSS, CSRF, sem login) + fumaça de assets · sem `innerHTML` em deltas.
- F2 Tokens por resposta/agregados + backup/restore · RNF-02/RF-02 ·
  `management/commands/backup_db.py` · `test_backup.py` (API backup SQLite) · restore íntegro.

## G — Fechamento
- G1 Suíte verde sem rede + ruff · todos · `pytest`, `ruff check` · 100% passando.
- G2 README/CLAUDE.md/learning-guide/verification · — · docs · revisão manual.
