# Arquitetura + ADRs

```
Navegador (chat, seleção Ferramentas, cartões aprovar/recusar)
  ↓  fetch POST + CSRF + SSE incremental
chat/views/          api_tools.py (aprovações, continuação), api_runs.py (SSE),
                     api_conversations.py (preferências), mcp_views.py (conexões)
chat/services/tools/ registry.py (unificado) · policy.py (allow/require/deny)
                     local_tools.py (calculate, current_time, *_study_note)
                     executor.py (validação, limites, idempotência)
                     approvals.py (expiração, consumo atômico)
                     loop.py (etapas Anthropic, tool_use→tool_result)
                     mcp_client.py (stdio/HTTP via SDK, sem guardar sessão)
                     context.py (ExecutionContext confiável)
chat/models_tools.py ModelStep, ToolInvocation, ToolApproval, MCPConnection,
                     ToolCatalogSnapshot, ConversationToolPrefs, StudyNote
mcp_servers/study_lessons/ servidor demo (stdio + Streamable HTTP)
```

## ADRs

- ADR-T1 Loop explícito próprio (não tool runner automático): autorização,
  pausa humana e retomada exigem pontos de controle que runners escondem.
- ADR-T2 Uma chamada de provedor por etapa (`messages.create` não-stream no
  loop de ferramentas; streaming de texto só na etapa final de resposta —
  simplifica `input_json` parcial: nunca executa de delta). [R7, R8]
  Revisão: o loop usa `create` (não `stream`) nas etapas com ferramentas;
  a etapa de texto final pode usar o stream existente.
- ADR-T3 Aprovação = linha no banco com consumo atômico, nunca estado em
  memória (`asyncio.Event` proibido p/ espera humana).
- ADR-T4 MCP via SDK oficial (`ClientSession` + transports); sem handshake
  manual. Sessões nunca cruzam event loop: abrir→usar→fechar na mesma tarefa.
- ADR-T5 `mcp_servers` remoto do provedor documentado, não implementado
  (perderíamos pausa/autorização antes do efeito). [R1, R3]
- ADR-T6 Nomes Anthropic `local__<nome>` / `mcp__<alias>__<nome>` + hash curto
  em colisão; mapeamento persistido por (conexão, original) → nome. [R2, R6]
