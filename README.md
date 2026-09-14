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
- Fora da v1 (backlog): anexos, OCR, RAG, ferramentas, ramificações,
  compartilhamento público, sincronização, resumo por IA, custo estimado.
