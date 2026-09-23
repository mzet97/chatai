# Modelo de ameaças (M0/M2) — OWASP AI Agent + RAG Cheat Sheets [R16]

Escopo: local-lite + contratos M2. Homelab (OIDC, S3, broker) revalida em M3/M4/M6.

## Superfícies e controles

| # | Ameaça | Onde | Controle implementado | Teste/evidência |
|---|---|---|---|---|
| T-1 | Prompt injection via documento/imagem/MCP autoriza efeito | RAG, tools, subagentes | Entrada não confiável nunca vira system; schemas validados; subagente só leitura; aprovação vinculada a operação+args+versão+usuário+execução | testes injection existentes; `test_runs_durable_m2` (escopo) |
| T-2 | Subagente amplia acesso (outro usuário, conversa, fonte) | delegação | interseção de permissões; IDs do modelo não substituem autorização; `owned_*` em todas as rotas novas | `test_cross_user_run_is_hidden`; testes M4 |
| T-3 | Modelo cria agentes/permissões ou edita política | API agentes | sem criação dinâmica; instruções não concedem permissões; sem chave por agente | testes M1 perfis |
| T-4 | Cache conserva dado revogado | cache/replay | revogação prevalece; reconstrução segura ou interrupção; fingerprint inclui revisão de ACL | testes revogação M3/M5 |
| T-5 | Replay de assinatura incompatível / cross-provedor | replay | blocos por agente, sem copiar assinatura; troca de provedor exige novo segmento (sem fallback) | testes replay; `ProviderUnavailable` |
| T-6 | Duplo clique / duas abas duplica turno ou efeito | comandos | idempotency_key + content_hash (409 em divergência); exclusividade `active_run` | `test_command_idempotent_and_conflict` |
| T-7 | Efeito ambíguo pós-queda reexecutado | worker/claims | efeito `unknown` até reconciliação; sem reagendar escrita por expiração de lease; massacre por CAS | state-machines.md; M2 worker |
| T-8 | Vazamento por rota de recuperação (SSE/eventos/export) | eventos | projeção liberada (`_project`, `journal._sanitize`); sem segredos/assinaturas/Base64; revalidação ao reconectar | teste SSE replay |
| T-9 | CSRF em mutações novas | rotas M2 | CSRF nas mutações (padrão Django); GET nunca muta | suíte + e2e |
| T-10 | Exfiltração para outro provedor (fallback silencioso) | providers | registro recusa não-Anthropic; sem telemetria/avaliação externa; teste varre `import openai` | `test_no_openai_dependency_at_runtime` |
| T-11 | MCP stdio arbitrário no cluster (futura) | M4/M6 | proibido cadastrar stdio arbitrário; sem executar terceiros no pod com chaves; OAuth ≠ OIDC login | ADR isolamento MCP; sem código ainda |
| T-12 | Backup antigo apaga conversas novas | M3 | rollback só pré-corte; pós-corte exige export/reconciliação; nunca restore cego | migration.md (M3) |

## Não alegado

Sandbox/DLP/isolamento físico, certificação, HA, teto monetário exato —
nada disso é prometido (§26). Lacunas conhecidas: fencing token real e
lease distribuído ficam para M3/PostgreSQL (U-1…U-5 em conflicts.md).
