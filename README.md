# claude-chat-local

Chat web em português com IA: **interface e armazenamento (SQLite) locais**;
a **inferência ocorre na API da Anthropic** — o contexto selecionado de cada
chamada sai do computador. Este exercício ensina o SDK oficial (`anthropic`)
na prática; não cobre certificação alguma.

## Requisitos

macOS ARM64, Python 3.13, `.venv` própria do projeto.

## Início rápido (comandos de terminal)

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env   # edite: ANTHROPIC_API_KEY e DJANGO_SECRET_KEY
.venv/bin/python manage.py migrate
.venv/bin/python manage.py create_local_user --username local
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python -m uvicorn config.asgi:application --host 127.0.0.1 --port 8000
```

Abra http://127.0.0.1:8000, faça login e inicie o primeiro chat. Com chave
válida, funciona sem configuração avançada. O mesmo comando uvicorn serve os
assets estáticos (handler ASGI próprio, ver `docs/adr/006-static-asgi-proprio.md`).

**1 worker.** SQLite + streaming SSE foram validados com um worker; não anuncie
implantação distribuída nem múltiplos workers.

## Uso diário

- Enter envia, Shift+Enter quebra linha; Interromper preserva o parcial
  (conteúdo já processado pode ter custo).
- Painel “Contexto” mostra o snapshot da última execução (sem credenciais).
- **Ferramentas** (botão na barra do chat, por conversa, desativadas por
  padrão): `calculate`, `current_time` (automáticas após ativação),
  `create_study_note` (sempre pede aprovação), `list_study_notes`, mais
  ferramentas de servidores MCP concedidos a você. A trilha de atividade
  mostra solicitada → aguardando aprovação → executando → concluída/
  recusada/falhou. Cartões de aprovação têm “Aprovar uma vez” e “Recusar”;
  texto no chat nunca aprova nada.
- Exportar (MD/JSON) contém conteúdo **privado**; excluir a conversa não apaga
  cópias exportadas nem backups.
- Fechar a aba durante geração equivale a interromper; reabrir mostra o
  persistido, sem gerar outra resposta.

## Configuração

Precedência (não secretas): conversa → interface → ambiente → `.env` → padrão.
Credencial: Keychain (se selecionada) → ambiente → `.env`. A tela de
Configurações mostra a origem efetiva, nunca o segredo. `.env` exige reinício;
interface vale nas próximas chamadas. Troca de endpoint exige confirmação e
passa pela allowlist do servidor (`api.anthropic.com`).

## Operação

```bash
.venv/bin/python -m pytest tests -q          # suíte padrão (sem chave, sem rede)
.venv/bin/python -m playwright install chromium  # só para tests/e2e (navegador real)
.venv/bin/ruff check chat config tests && .venv/bin/ruff format --check chat config tests
.venv/bin/python manage.py backup_db --out backups/chat.sqlite3
.venv/bin/python manage.py restore_db --src backups/chat.sqlite3  # com a app parada
```

Teste pago opcional (desativado por padrão, 2 chamadas pequenas):
`CHAT_LIVE_TEST=1 ANTHROPIC_API_KEY=... python -m pytest tests/integration/test_live.py -q`.
Nunca roda em CI/verificação padrão.

Remover credenciais: Configurações → “Remover chave” (+ apague do `.env`).

## Proteção e limites reais

- SQLite local **não é criptografado**: proteção = permissões do SO + máquina.
- Chave nunca sai do backend (só vai à API no endpoint configurado); nunca em
  HTML/JS/JSON/logs. SQLite guarda só referência do Keychain.
- Sem telemetria; sem CDN; sem preços/saldo inventados (só tokens observados).
- Fora da v1 (backlog): anexos, OCR, RAG, ramificações,
  compartilhamento público, sincronização, resumo por IA, custo estimado.

## Ferramentas e MCP (didático)

**Tool calling × MCP.** Tool calling = o modelo pede, *este app* executa
(local em `chat/services/tools/`) ou chama um servidor MCP aprovado, e o
resultado volta ao modelo. MCP = só o protocolo app↔servidor (SDK MCP
oficial); o app continua sendo o host que autoriza tudo. Não usamos
`mcp_servers` remoto da Anthropic nem tools do provedor (web search, code
execution).

**Blocos de protocolo.** O modelo devolve `tool_use` (id + nome + args); o
app responde `tool_result` com o mesmo id, em mensagem `user` logo após a
do assistente. Nada executa a partir de JSON parcial de streaming.

**Autorização.** Três decisões no backend: auto (leituras locais seguras),
exigir aprovação (escritas e tudo externo), negar. Aprovação = endpoint
protegido (sessão + CSRF + idempotência), uso único, 10 min, vinculada a
usuário/conversa/execução/args normalizados/schema. Sem aprovação, a run
pausa (`awaiting_approval`) e o HTTP encerra; continuar retoma do estado
persistido, sem re-perguntar ao modelo.

**Dados por destino.** À Anthropic: system + histórico + catálogo autorizado
+ resultados (nunca credenciais). Ao MCP: só os argumentos aprovados (nunca
o histórico). Na UI/SSE/inspetor: nomes e resultados como texto escapado
(nunca HTML do servidor, nunca segredos).

**Demonstração sem chave.** Servidor demo em `mcp_servers/study_lessons/`
(`list_lessons`/`read_lesson`): `python -m mcp_servers.study_lessons.server`
(stdio) — ver `docs/tools-mcp/`. Testes: `pytest tests -q` (tudo simulado +
servidores controlados reais).

**Limitações honestas.** Sem sandbox de SO além do usuário local; sem DLP;
stdio confia no binário listado. `unknown` após queda sem confirmação (sem
retry, sem exatamente-uma-vez entre sistemas). **Não executado:** Keychain
ao vivo, API Anthropic real paga, OAuth ao vivo contra provedor externo.
