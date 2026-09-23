# ADRs — Thinking + Visão

Decisões curtas. Contexto completo em `spec.md`; contratos em
`contracts.md`; execução em `plan.md`.

## ADR-1 — Desconhecido = bloqueado (tabelas por ID exato)

Modelo fora da tabela, sem modelo ou endpoint customizado → `unknown`:
controles desabilitados, chaves omitidas, preferência preservada. Nunca
inferir por nome/família nem copiar um `thinking.type` para todos. Espelha
`variation.py` (temperatura: `TEMPERATURE_SUPPORTED/UNSUPPORTED_MODELS`,
prova ao vivo Sonnet 5 + `temperature` → 400). Conflito metadados × docs →
bloqueia + registra, nunca o permissivo.

## ADR-2 — `display` só com pensamento explícito

`display="summarized"|"omitted"` só quando o pensamento foi explicitamente
ativado e o suporte é confirmado. Modo `default` nunca liga pensamento só
para obter resumo; `disabled` nunca envia `display` (inválido no SDK).
`show_summary` é preferência visual separada de ligar o pensamento.

## ADR-3 — Conflito budget×teto bloqueia antes da chamada

`budget_tokens < max_tokens` verificado no backend antes de qualquer chamada
paga; conflito (ex. 2.048 × teto 1.024) retorna `conflict` + motivo e oferece
total confirmado. Sem exceção de interleaving, sem aumento silencioso de
limites. Pensamento detalhado é subconjunto da saída — nunca somado a ela.

## ADR-4 — Anexo de conversa ≠ documento RAG

Anexos de imagem não entram no pipeline RAG (sem chunk/embedding/índice,
sem virar fonte citada). Reutiliza-se só o **padrão** cliente (`upload.js`:
FormData, progresso, retry) e servidor (idempotência, validação estrita);
a rota e o modelo são separados. Sem chamada oculta de descrição/OCR; sem
OCR como substituto de visão; Files API fora de escopo.

## ADR-5 — Base64 só na serialização

O navegador nunca envia Base64; o servidor nunca estoca Base64 em
snapshots, logs ou campos de projeção. O bloco `image` é montado na hora de
serializar a chamada a partir da variante normalizada imutável. Limites
binários (ferramentas MCP com imagem, anexos) separados do teto de texto —
nunca truncar Base64 para 32 KiB.

## ADR-6 — Protocolar × projeção; replay integral

Blocos integrais de pensamento (incl. opacos/`signature`) persistem no
registro protocolar versionado; a UI recebe só projeções sanitizadas, e só
quando autorizadas. Replay entre turnos reemite blocos completos +
`tool_result` na ordem exigida; mudança de prefixo → nova versão + aviso de
reinício do raciocínio. Nada é inventado; sem segunda chamada para fabricar
resumo.
