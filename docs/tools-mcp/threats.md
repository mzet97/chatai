# Ameaças (OWASP AI Agent + MCP security)

- T-A Injeção via descrição/schema/resultado MCP → escrita sem aprovação.
  Controles: política no backend (annotations são alegações), aprovação de
  escrita sempre, args validados por schema próprio, sem system prompt a partir
  de conteúdo externo. Teste §17.12.
- T-B Confusão de ferramenta (colisão de nomes entre servidores). Controles:
  nomes `mcp__<alias>__<nome>`+hash, mapeamento persistido, catálogo congelado
  por execução. Teste §17.9.
- T-C Exfiltração: histórico completo ao MCP; segredo em args/logs/SSE.
  Controles: só args aprovados; trava de segredos antes de transferir;
  credenciais só por referência Keychain. Testes §17.10/15.
- T-D stdio malicioso/overprivilegiado. Controles: allowlist de executáveis
  (demo + confiáveis explícitos), sem shell/download, env mínimo sem segredos,
  sem promessa de sandbox (documentado). Teste §17.10.
- T-E SSRF/OAuth (HTTP). Controles: allowlist de destinos, HTTPS (loopback só
  demo), sem TLS-off, PKCE+estado único+issuer/resource, sem reuso entre
  recursos. Teste §17.11.
- T-F Aprovação forjada (texto no chat, `approved:true`, DOM/JSON).
  Controles: endpoint CSRF+idempotente, digest de args, consumo atômico,
  revalidação pré-efeito. Teste §17.5.
- T-G Repetição/duplicação (duplo clique, 2 abas, retry, reinício).
  Controles: chave de operação única, consumo atômico, efeito `unknown` sem
  retry auto. Testes §17.6/8.
- Sem DLP/sandbox efetivos na infra: documentado, não compensado com regex
  como única fronteira.
