## Goal

Eliminar os 500s evitáveis, fechar os gaps de validação/auth e concluir os itens M6/docs pendentes apontados na revisão, sem mudar comportamento contratado nem adicionar dependências.

## Success Criteria

- Nenhum `assert` em caminho de request; env inválido e corpo inválido retornam 4xx com código estável, nunca 500.
- Todas as páginas exigem login de forma consistente; PATCH de conversa valida tipos/limites.
- Falha de tool vira `tool_result` com erro classificado, nunca turno silencioso.
- Deploy reproduzível do zero (pull secret versionada, tag parametrizada, docs sincronizados).
- Suíte verde + preflight `--cluster` verde ao final de cada fase.

## Context And Current Facts

- `assert outcome…approval_required` em `chat/services/tools/chat_loop.py:428`; some com `python -O`.
- `int(raw)` sem guarda em `chat/services/configuration.py:58`; `json.loads` sem `try` em 6 endpoints (`api_conversations.py:110,151`, `api_messages.py:34,96`, `settings_views.py:51,137`).
- `settings_page` sem `@login_required` em `chat/views/pages.py:43` (as demais têm); `PATCH` faz `setattr` cru em `title`/`preferred_model`/`system_prompt` (`api_conversations.py:152-155`).
- Executor captura só `TimeoutError`; `_finalize` (`generation.py:949-65`) sem guarda para a segunda falha.
- Deploy: `imagePullSecrets` criada na mão (fora do repo), tag `m6-4` fixa no yaml enquanto RUNBOOK cita `m6-2`; sem `securityContext`/limits de CPU.
- OIDC é só validação (`identity.py`); callback não existe. Docs com `sed -i ''` (BSD-only) e trechos desatualizados.

## Constraints And Non-goals

- Sem `DJANGO_ALLOW_ASYNC_UNSAFE`, sem `select_for_update`, sem Redis/Celery/Docker extras; SDK só em `services/`.
- Sem chamadas pagas automáticas; sem commit/push/deploy sem sua autorização explícita por fase.
- Não inclui: callback OIDC real contra Authentik (depende do operador), HPA/autoscaling, migração de banco.

## Key Decisions

- **Ordem por risco, não por arquivo**: 500s e auth primeiro (fase 1), robustez do loop depois (fase 2), deploy/docs por último (fase 3). Rejeitado fazer deploy primeiro: reproduziria os 500s na imagem nova.
- **Códigos de erro estáveis existentes** (`validation`, `unauthorized`, etc.), sem criar taxonomia nova.
- **OIDC fora do plano**: sem provider acessível, implementar callback seria código não verificável; fica como lacuna declarada.
- **Sem novas dependências**: tudo com stdlib + Django + padrões já usados (`classify_error`, `sync_to_async`).

## Recommended Approach

Três fases sequenciais, cada uma com testes e gate antes da próxima. Correções mínimas na causa raiz, espelhando os helpers existentes de cada área.

## Work Plan

**Fase 1 — 500s e auth (risco alto, esforço baixo)**
1. `tools/chat_loop.py:428`: trocar `assert` por checagem explícita que retorna erro `validation`.
2. `configuration.py`: `resolve_int` com `try/except (ValueError, TypeError)` → erro de config 4xx; validar `ANTHROPIC_BASE_URL` antes do localhost-bypass.
3. Helper `parse_body(request)` com `try json.loads` → `{"code":"validation",…}` 400; usar nos 6 endpoints.
4. `@login_required` em `settings_page`; verificar `AnonymousUser` não quebra o render.
5. `PATCH` conversa: validar `title` (≤200), `preferred_model` (não-vazio, ≤200), `system_prompt` (≤ limite existente) com `validation` 400.
6. Testes: corpo inválido × 6 endpoints, env inválido, PATCH inválido, settings anônima → login.

**Fase 2 — robustez do loop de tools (risco médio)**
1. Executor: capturar `Exception` além de `TimeoutError`, classificar via `classify_error` e devolver `tool_result` de erro (nunca turno silencioso).
2. `_finalize` com guarda: falha na persistência final gera evento/ledger em vez de escapar.
3. `_file_env` lido sob demanda (ou documentar stale) em vez de só no import.
4. Testes: tool que levanta, falha dupla no finalize, catálogo vazio × default-vazio (`loop.py:69`).

**Fase 3 — deploy reproduzível + docs (risco baixo)**
1. Versionar pull secret (manifesto `docker-registry` com placeholder) + documentar criação; parametrizar tag da imagem (envsubst/kustomize, sem tag fixa).
2. `securityContext` (non-root, readOnly quando viável) + limits de CPU; `PGSSLMODE`/vars PG documentadas no ConfigMap-exemplo.
3. Docs: `sed` portátil, rollback com banco, sincronizar tag M6 e seção cluster; `SECRET_KEY`/`DEBUG` com trava para perfil postgres.
4. Validação: `apply --dry-run=server` do zero + preflight `--cluster` + smoke `/api/health`.

## Validation Plan

- Fase 1: `.venv/bin/python -m pytest tests/integration/test_conversations*.py tests/integration/test_security.py -q` + probes manuais de corpo inválido; gate: zero 500 nos casos.
- Fase 2: testes novos de tool-falha e finalize-falha; gate: todo turno tem `tool_result` ou evento.
- Fase 3: `kubectl apply --dry-run=server -f deploy/` em namespace limpo + `.venv/bin/python manage.py preflight --cluster --format json` com todos `ok`; gate: reprodução do zero documentada.
- Final: suíte completa `pytest tests -q` + `ruff check` + `ruff format --check`.

## Risks / Rollback

- Mudar `resolve_int` pode expor envs antes silenciosos → mitigado por testes de config em cada perfil (`sqlite`/`postgres`).
- Endurecer PATCH pode rejeitar clientes antigos → códigos `validation` já contratados pelo frontend.
- Rollback por fase: `git stash`/revert dos arquivos da fase (sem commit intermediário sem autorização).

## Open Questions

None — nenhuma decisão de produto; OIDC real e HPA declarados non-goals acima.
