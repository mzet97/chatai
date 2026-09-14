# ADR-004 — Segredo só em Keychain/ambiente/.env; nunca no SQLite em texto puro

Data: 2026-09-14 · Estado: aceita.

Contexto: RF-05 exige Keychain macOS via `keyring`, sem fallback silencioso.
Decisão: SQLite guarda só `keychain_ref` + metadados. Se Keychain indisponível,
a origem restante é `.env`/ambiente; a interface informa, sem gravar texto puro.
Alternativas descartadas: coluna `api_key` cifrada "na mão" (falsa segurança sem
gestão de chave); localStorage/cookies (expostos a XSS).
Consequências: no Linux/CI sem Secret Service, usa-se `.env` (documentado).
