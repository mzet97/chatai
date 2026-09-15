# Tools + MCP — Especificação (RF-15)

Extensão didática do `claude-chat-local`: a IA solicita ferramenta → backend
valida/autoriza → executa (Python local ou servidor MCP) → resultado volta à IA
→ resposta final ou estado explícito. Sem autonomia irrestrita.

## Requisitos

- T1 Ferramentas locais: `calculate` (op enum, 2 números limitados, sem
  eval/exec), `current_time` (fuso IANA validado), `create_study_note`
  (título/texto limitados, exige aprovação), `list_study_notes` (só da
  conversa atual). `ExecutionContext` servidor define usuário/conversa;
  IA nunca escolhe destino.
- T2 Registro unificado: identidade estável, origem, nome original, nome
  Anthropic, descrição, schema, versão/hash, limites, política de aprovação.
  Exposição por chamada = interseção (admin ∩ usuário ∩ conversa ∩ modelo).
  Tudo começa desativado; descoberta ≠ autorização.
- T3 Loop explícito: até 6 gerações e 8 invocações/solicitação; 20 s por
  ferramenta; 180 s ativos (fora espera humana); ≤20 ferramentas expostas;
  32 KiB por resultado. `tool_use` preservado no bloco assistant; `tool_result`
  em mensagem `user` com IDs originais; sequencial determinístico.
- T4 Aprovações: permitir / exigir / negar. Leitura segura auto após ativação;
  escrita e MCP exigem aprovação. Aprovar-uma-vez / recusar via endpoint
  CSRF+idempotente; expiração 10 min, consumo único, vínculo
  (usuário, conversa, execução, args normalizados, revisão, schema, política).
  Sem aprovação → `awaiting_approval` + `run_paused` + HTTP encerrado.
- T5 Retomada continua a execução persistida (não re-pergunta ao modelo);
  revalida política/conexão/schema/aprovação. Efeito pós-queda sem confirmação
  = `unknown`, sem retry automático. Turno e ação têm estados separados.
- T6 MCP: cadastro admin (alias, transporte, config não secreta, ref credencial,
  revisão); `stdio` (absoluto, sem shell, env mínimo, sem segredos) e Streamable
  HTTP (HTTPS; loopback só p/ demo; SSRF/OAuth verificados). Demo `study-lessons`
  com `list_lessons`/`read_lesson` (IDs do conjunto, stdio + HTTP).
- T7 Streaming: mesmos eventos + `model_step_started`, `tool_call_requested`,
  `tool_approval_required`, `tool_started`, `tool_finished`, `run_paused`.
  `done` só no fim do turno inteiro. Completo retém texto mas mostra aprovações.
  Nunca executa de `input_json_delta` parcial.
- T8 UI: controle **Ferramentas** por conversa; **Conexões MCP** em
  Configurações; trilha de atividade; cartões de aprovação com foco/teclado e
  anti-duplo-clique; painel didático sem credenciais. Sem botões de recurso
  inexistente.
- T9 Auditoria: `ModelStep`, `ToolInvocation`, `ToolApproval`, `MCPConnection` +
  snapshot de catálogo, `StudyNote` (operação única). Export versionado sem
  segredos. SQLite: transações curtas, sem `select_for_update`, sem transação
  em rede/aprovação.
- T10 Segurança: descrições/resultados são não confiáveis (nunca system prompt);
  só args aprovados vão ao MCP; sem `approved=true` da IA; sem regex como única
  fronteira; sem DLP/sandbox inventados.

Fora desta versão: marketplace, agents background, shell/SQL genérico, filesystem
irrestrito, MCP Apps, resources/prompts automáticos, sampling/elicitation de
servidor, `mcp_servers` remoto do provedor (documentado, não implementado).
