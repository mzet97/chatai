# Plano / tarefas

- M1 `chat/services/tools/{registry,local_tools,executor,loop,limits,context}.py`
  + `tests/unit/test_tool_registry.py`, `tests/integration/test_tool_loop.py`
  (fakes Anthropic+MCP): leitura/cálculo, ciclo 1 chamada, multi-chamadas,
  JSON inválido/desconhecida não executam, sem ferramentas = sem MCP.
- M2 `chat/models_tools.py` (StudyNote, ToolInvocation, ToolApproval) +
  migration; `approvals.py`; endpoints aprovar/recusar/continuar; testes
  aprovação/recusa/adulteração, idempotência duplo-clique, expiração, retomada
  pós-reinício (SQLite arquivo), efeito `unknown` sem retry.
- M3 `mcp_client.py` (stdio via SDK, env mínimo, mesma-tarefa) +
  `mcp_servers/study_lessons/` (MCPServer, `list_lessons`/`read_lesson`,
  stdio + HTTP) + testes de protocolo sem Anthropic + teste ciclo MCP.
- M4 `MCPConnection` + snapshots + SSRF/allowlist + Bearer/Keychain + OAuth
  (SDK oficial, PKCE, estado único) + testes de rejeição; HTTP loopback só demo.
- M5 `ConversationToolPrefs` + controle **Ferramentas** + trilha de atividade +
  cartões de aprovação + painel didático + eventos SSE novos nos dois modos de
  entrega + e2e navegador.
- M6 Falhas/recuperação (paginação, colisão, troca de catálogo, isolamento,
  injeção, limites pós-retomada, XSS/vazamento, redução de contexto com pares
  intactos) + ASGI real + docs didáticas + README/CLAUDE.md.

Evidências em `docs/tools-mcp/evidence.md` (comandos + resultados reais).
