# Guia de estudo — claude-chat-local (SDK da Anthropic na prática)

Este exercício ensina o SDK oficial num app real. Não promete certificação.
Exemplos fictícios, sem dados pessoais ou segredos.

## 1. `Anthropic` × `AsyncAnthropic` e a chamada real

- `chat/services/anthropic_client.py:build_client` cria `AsyncAnthropic` com
  `api_key`, `base_url`, `timeout`, `max_retries` explícitos. O app usa o cliente
  **assíncrono** porque o streaming roda no event loop do uvicorn; o SDK
  síncrono bloquearia o loop.
- A chamada real está em `chat/services/generation.py:execute_run`:
  `async with client.messages.stream(model=..., messages=..., system=...,
  max_tokens=...) as stream`, deltas via `stream.text_stream`, final via
  `await stream.get_final_message()` — o mecanismo documentado do SDK.

## 2. Por que reconstruir o histórico (papel do SQLite)

A Messages API é stateless. `chat/services/context_builder.py:build_context`
monta `turnos anteriores válidos + mensagem atual 1x` a partir do SQLite
(`_history_for`), nunca do que o navegador afirma. Só resposta `state="ok"`
entra; falhas/canceladas ficam visíveis, fora do contexto.

## 3. `system`, `messages`, blocos e isolamento

`system` vai no nível superior; `messages` só tem `user`/`assistant`
(`test_context_builder.py`). `_parse_final` lê **todos** os blocos `type=text`,
sem assumir `content[0]`. Isolamento: toda query filtra `owner` + conversa
(`chat/views/_scoping.py`); v1 sem tools/thinking.

## 4. Streaming, encerramento e parciais

SSE: `run_started → text_delta* → usage → done|error` (`api_runs.py`). Deltas
são texto puro (`textContent`); Markdown só ao concluir (`markdown.js`).
Checkpoints espaçados (2s/4KB), nunca por token. Cancelar fecha o stream
upstream e persiste o parcial como `cancelled`; aba fechada vira `interrupted`;
nunca `done`. `done` só sai após a persistência final.

## 5. Contagem × uso final e orçamento

Antes de gerar: `messages.count_tokens` (`_bind_counter`). Estouro remove
turnos completos antigos e reconta; modo estrito bloqueia. Uso final do
`get_final_message` **substitui** contadores (nunca soma). Desconhecido = null.

## 6. Configuração, `.env`, Keychain e erros de auth

Precedência em `configuration.py:resolve_option/resolve_credential`; `.env`
lido da raiz com `dotenv_values`, sem mutar `os.environ`. SQLite guarda só
`keychain_ref`. `classify_error` traduz 401/403/404/429/timeout/conexão em
códigos estáveis — sem culpar “saldo”.

## 7. Migrations, testes e simulação do SDK

Modelo em `models.py`, migrations geradas. `tests/fakes.py` simula o SDK no
formato de uso (injeção por `client=`), sem monkeypatch de internos. Banco de
teste em arquivo: threads e `sync_to_async` enxergam o mesmo banco.

## 8. SDD e mudanças pequenas

Leia `CLAUDE.md` → mude pouco → `pytest` + `ruff` → confira. Rotas e contratos
em `docs/architecture.md`.

## Exercícios

1. Mude o system prompt da conversa, gere, e confira o snapshot no painel Contexto.
2. Troque o modelo e verifique `requested_model` × `actual_model` no run.
3. Reduza o orçamento e observe `omitted_turns` (e o modo estrito bloqueando).
4. Simule 401 (chave inválida no diagnóstico) e leia o passo que falha.
5. Interrompa uma resposta no meio, reabra a conversa e confira o parcial.
6. Envie duas vezes com a mesma `idempotency_key` (replay) e com conteúdo
   diferente (409). 7. Rode `backup_db`/`restore_db` e confira o `integrity_check`.

## 9. Tool calling e MCP (o app como host)

1. O modelo nunca executa nada: ele devolve blocos `tool_use` (id + nome +
   argumentos). Quem executa é o app, depois de validar e autorizar.
2. O app responde com `tool_result` (mesmo id) em mensagem `user` logo após
   a do assistente — e chama o modelo de novo. Uma resposta pode ter várias
   etapas; `done` só encerra o turno inteiro.
3. Três decisões no backend: `auto` (leituras locais seguras), `require`
   (escritas e tudo externo: pede aprovação) e `deny` (bloqueado).
4. MCP é só o transporte app↔servidor (SDK oficial). O catálogo exposto ao
   modelo é a interseção: prefs da conversa ∩ disponível (nunca tudo).
5. Sem aprovação, a execução pausa (`awaiting_approval`), o HTTP encerra e
   nada fica esperando em memória. Continuar retoma do banco, sem
   re-perguntar ao modelo — e revalida prefs, schema e aprovação.
6. Descrições e resultados MCP são texto não confiável: nunca entram no
   system prompt, nunca viram HTML, nunca autorizam nada.

Exercícios: ative `calculate` numa conversa e peça uma conta (trilha mostra
as etapas); peça uma nota, recuse, aprove a nova tentativa; abra o inspetor
da run (`run_detail.tools`) e confira etapas, invocações e aprovações.
