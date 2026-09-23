# Correção SDD — Upload de documentos integrado ao RAG

## Diagnóstico (com evidência)

Rastreado botão → worker no app real (conta de teste, Chromium headless):

| Elo | Estado encontrado |
|---|---|
| POST `/api/rag/bases/<uuid>/upload` | existia (202 + job), arquivo único |
| Página Conhecimento | Criar base, upload de 1 arquivo, lista, detalhes, excluir |
| Diálogo na conversa (Fontes) | **ausente** — só link "gerenciar bases" |
| Entrada no estado vazio | **ausente** — upload escondido até selecionar base |
| Múltiplos arquivos, arrastar, progresso | **ausentes** (`files[0]`, `fetch` sem `upload.onprogress`) |
| Erro de envio | **exceção sem catch** (rejeição não tratada, sem mensagem) |
| Idempotência (`client_key`) | **ausente** — reenvio duplicava documento/job |
| Conteúdo idêntico | **duplicava** (`existing` consultado no doc recém-criado, sempre vazio) |
| Nome igual, conteúdo novo | **sobrescrevia em doc novo sem escolha** |
| Retry ("Tentar novamente") | **ausente** (sem endpoint) |
| Worker offline/online | **ausente** (só texto estático "Rode o worker") |
| "Usar nesta conversa" | **ausente** |
| Cobertura pronta × processando | **ausente** em Fontes |
| Comando dev (servidor+worker) | **ausente** |
| `CHAT_RAG_DIR` isolável | **ausente** (só `CHAT_DB_PATH`) |

Teste que reproduzia a ausência: `tests/e2e/test_rag_upload_browser.py`
(criado junto; falhava em `#sources-upload` inexistente e no fluxo).

Achados durante a correção (bugs reais, com prova):

1. Uploads concorrentes (2 XHR do lote) → `OperationalError: database is
   locked` → HTTP 500 em um dos arquivos (reproduzido com 2 threads e no
   teste de navegador). Correção: `_busy_retry` com backoff nas escritas de
   upload/retry + reversão de arquivos órfãos da tentativa; teste comitado
   `test_uploads_concorrentes_sem_500`. Transações continuam curtas; o
   `busy_timeout` de 10s sozinho não absorveu a contenção observada.

2. `knowledge.js` gravado truncado na primeira versão (marcador literal
   `...[truncated...` no arquivo) → `SyntaxError` só visível no navegador;
   `node --check` não acusou. Reparado e coberto pelo teste de navegador
   (assert sem `pageerror`).
3. `send()` limpava o composer após o POST de reserva; texto digitado no
   intervalo era apagado (rascunho "consumido"). Prova: interceptação do
   setter com pilha apontando `chat.js:558` dentro de `send()`. Agora limpa
   no ato do envio e restaura em caso de falha ("Seu texto foi mantido").

## Tarefas executadas

- Backend: `client_key` (migração 0013), dedup por conteúdo, 409
  `name_conflict` + `on_name_conflict=version|separate`, `POST
  jobs/<uuid>/retry` (geração+1), heartbeat em arquivo + `GET
  worker/status`, `manage.py dev`, `CHAT_RAG_DIR`, cobertura em
  `conversation_sources`, tamanho/data na lista de documentos.
- Frontend: `upload.js` compartilhado (diálogo, dropzone + input múltiplo
  ≤10, XHR com progresso, 2 concorrentes, polling 2s, fases do backend,
  retry, conflito explícito, "Usar nesta conversa", foco/a11y); entradas em
  Conhecimento (estado vazio incluso) e Fontes (com cobertura); CSS
  unificado + `.btn.danger`.
- Testes: `test_rag_upload_api.py` (9), `test_rag_upload_browser.py`
  (aceite ponta a ponta, ingestão real, geração simulada declarada).

## Evidências

- Aceite em Chromium: base "Aurora" criada inline, `aurora.txt` com fato
  exclusivo → "Recebido" → worker real → "Pronto para consulta" → reload
  persiste → Fontes seleciona (1) → evidência `kb://…` com "quintas"
  recuperada. Sem erros de console/página. Screenshots em `/tmp/shot_up_*.png`
  (verificação manual; o teste comitado reexecuta o fluxo).
- Suite: ver relatório final da entrega (comando `pytest -q`).
