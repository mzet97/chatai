# Migração local-lite → homelab (M3)

Sem big-bang e sem sincronização bidirecional contínua. Fases: inventário/backup →
desacoplar contratos → runtime durável local → perfil homelab → ensaio em destino
separado → congelar escritas → verificar → liberar.

## O que é migrado

IDs, relações, proprietários, timestamps/timezones, JSON, snapshots, anexos, hashes
(SHA-256 próprio; ETag S3 não substitui, §14) e estados. Tarefas ambíguas
(`delivering` sem progresso, `unknown` em efeitos) **não** migram como prontas para
reexecutar: revalide antes de retomar.

## Procedimento ensaiado (local, sem Postgres/S3)

Teste: `tests/integration/test_migration_rehearsal_m3.py`.

1. `manage.py backup_db --out backup.sqlite3` (cópia consistente do SQLite).
2. `PRAGMA integrity_check` no backup → `ok`.
3. Anexar o backup como destino separado (`ATTACH DATABASE ... AS src`) e comparar
   contagens por tabela + conteúdo amostral com o manifesto de arquivos
   (`tests/integration/test_migration_rehearsal_m3.py::test_files_manifest_copy_and_verify`
   prova cópia por hash via `ObjectStore`).
4. Diferenças SQLite/PostgreSQL (constraints, sequências, timezones) são normalizadas
   com testes reais no destino; `DB_ENGINE=postgres` exige `psycopg` + `PGHOST` e
   falha com mensagem útil caso contrário.

## Arquivos e índices

Arquivos copiados para o S3 com hashes e manifesto; índices (FTS/Elastic) são
**reconstruídos** a partir de dados/artefatos autorizados e comparados — nunca
migrados como fonte de verdade. Identidade OIDC associada somente após verificação.

## Rollback

- Antes do corte: voltar ao local intacto (backup + arquivos) é simples.
- Depois de novas escritas no homelab: rollback exige export/reconciliação ou perda
  explicitamente aceita. Nunca restaurar backup antigo por cima de conversas novas.
- Rollback de imagem não desfaz migration destrutiva: mudanças de esquema usam
  expand/contract.
