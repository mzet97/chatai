# Runbook — desbloqueio e fechamento do M6

Checklist do operador para sair do bloqueio atual (cluster verde, namespace
`chat-local` vazio) até o gate M6. Nenhum segredo neste arquivo: valores reais
só nos comandos `kubectl` executados pelo operador, nunca commitados.

## Bloqueio atual (22/09/2026)

- [x] Cluster saudável (`preflight --cluster` 10/10)
- [x] Manifests validados (`dry-run=server`)
- [ ] Imagem publicada
- [ ] Banco PG dedicado criado
- [ ] Secret `chat-local-secrets` criado
- [ ] `apply` + smoke + OIDC

## 1. Imagem

```bash
# Publicada via SSH no nó (build+push a partir de tarball sem
# .env/data/models/.venv): `harbor.home.arpa/tese/chat-local:20260922-m6-4`
# (Dockerfile em `deploy/Dockerfile`; RAG-E5 fora da imagem, via `rag_prepare`
# no runtime). Tags anteriores: m6-2 (original), m6-3 (fix CSRF).
```

## 2. Banco PG (host dedicado externo — nunca a base do Authentik)

Padrão validado (mesmo do Authentik): base com dono no usuário de serviço
(pg_hba libera por usuário+origem; role dedicado exigiria editar o servidor).

```sql
-- como db_admin, a partir do nó (origem permitida):
CREATE DATABASE chat_local OWNER db_admin;
```

Rollback inclui banco: `DROP DATABASE chat_local` (o app não migra para trás
sozinho; `migrate` só avança). Backup antes de cada apply com migração nova.

## 3. Secret (operador, via kubectl — nunca no repo)

```bash
kubectl -n chat-local create secret generic chat-local-secrets \
  --from-literal=DJANGO_SECRET_KEY='...' \
  --from-literal=PGHOST='...' \
  --from-literal=PGDATABASE='chat_local' \
  --from-literal=PGUSER='chat_local' \
  --from-literal=PGPASSWORD='...' \
  --from-literal=ANTHROPIC_API_KEY='...'
# OIDC (omitir as 4 chaves = login Django local):
# --from-literal=OIDC_ISSUER='https://authentik.home.arpa/application/o/.../' \
# --from-literal=OIDC_CLIENT_ID='...' \
# --from-literal=OIDC_CLIENT_SECRET='...' \
# --from-literal=OIDC_REDIRECT_URI='https://chat.home.arpa/oidc/callback'
```

## 3b. Pull secret do Harbor (uma vez por cluster limpo)

O containerd do k3s não usa o `docker login` do nó: sem isto, `ImagePullBackOff`.

```bash
kubectl -n chat-local create secret docker-registry harbor-tese-pull \
  --docker-server=harbor.home.arpa \
  --docker-username='<usuario>' --docker-password='<senha>'
```

Template versionado (placeholder, fora do apply): `deploy/templates/pull-secret.yaml`.

## 4. Apply (autorizado, nesta ordem)

```bash
TAG=20260922-m6-4  # trocar por build; portátil BSD/Linux:
sed -i.bak "s|harbor.home.arpa/tese/chat-local:[^\"' ]*|harbor.home.arpa/tese/chat-local:${TAG}|g" deploy/deployment.yaml && rm -f deploy/deployment.yaml.bak
kubectl apply -f deploy/namespace.yaml
kubectl apply -f deploy/configmap.yaml
kubectl apply --dry-run=server -f deploy/
kubectl apply -f deploy/
```

## 5. Smoke (gate M6)

```bash
kubectl -n chat-local rollout status deploy/chat-local-web
curl -s https://chat.home.arpa/api/health
# Esperado: {"status":"ok",...}
```

Depois: OIDC contra o Authentik (quando habilitado) + matriz de degradação
§21. Registrar o resultado em `docs/architecture-v2/verification.md`.

## Rollback

```bash
kubectl -n chat-local delete httproute chat-local-web
kubectl -n chat-local delete deploy chat-local-web
# Banco e Secret preservados (dados); re-apply = seções 4–5.
```
