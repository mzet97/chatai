# ADR-003 — Exclusividade por conversa via UPDATE atômico

Data: 2026-09-14 · Estado: aceita.

Contexto: no máximo uma execução ativa por conversa; SQLite não tem bloqueio de linha.
Decisão: `Conversation.objects.filter(uuid=..., active_run__isnull=True).update(...)`
condicional; 0 linhas afetadas = conflito (`409 run_busy`). Liberação em `finally`.
Alternativas descartadas: `select_for_update()` (no-op efetivo no SQLite);
lock em memória do processo (não cobre múltiplos workers/processos).
Consequências: correto sob concorrência real; `database is locked` tratado com retry
curto nas escritas de checkpoint.
