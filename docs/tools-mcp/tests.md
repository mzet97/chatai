# Testes (mapeamento §17 → arquivos)

| Item | Arquivo | O que prova |
|---|---|---|
| 1 | `test_tool_loop.py::sem_ferramentas` | catálogo vazio: sem `tools` no payload, sem processo MCP |
| 2 | `test_tool_loop.py::ciclo_local` / `::ciclo_mcp` | tool_use→exec→tool_result→resposta, IDs corretos |
| 3 | `test_tool_loop.py::multiplas` | N chamadas, N resultados pareados, nenhuma ignorada |
| 4 | `test_tool_loop.py::invalidas` | JSON inválido, schema incompatível, desconhecida: sem execução |
| 5 | `test_tool_approvals.py` | escrita aguarda; recusa; texto/JSON adulterado não aprovam |
| 6 | `test_tool_idempotency.py` | duplo clique, 2 abas, retomada, reinício: 1 efeito |
| 7 | `test_tool_policy.py` | expiração, schema trocado, conexão trocada, revogação: bloqueio |
| 8 | `test_tool_recovery.py` | queda pós-envio → `unknown`, sem retry automático |
| 9 | `test_mcp_catalog.py` | paginação, colisão, troca de catálogo, isolamento usuários |
| 10 | `test_mcp_stdio.py` | env sem segredos; sem órfãos em cancel/pausa |
| 11 | `test_mcp_http_oauth.py` | SSRF/redirect/estado/issuer/credencial cruzada rejeitados |
| 12 | `test_tool_injection.py` | injeção em descrição/resultado não escreve nem muda política |
| 13 | `test_tool_limits.py` | limites valem após retomada |
| 14 | `test_tool_streaming.py` | incremental × completo+aprovação; `done` só no fim do turno |
| 15 | `test_tool_leak.py` | código/HTML não executa; bloqueio não vaza por UI/logs |
| 16 | `test_tool_history.py` | reabertura reconstrói pares; redução preserva ciclos |

Padrão: modelo simulado (fake Anthropic com fila de respostas) + servidores
MCP controlados reais (demo `study-lessons` via stdio/HTTP local). ASGI real +
SQLite arquivo nos testes de entrega/concorrência. `test_live_tools.py`
opt-in (`CHAT_LIVE_TEST=1`), pequeno, sem afirmações sobre conteúdo do modelo.
