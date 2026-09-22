# Deploy interno M6 — perfil homelab (namespace `chat-local`)

Sem `apply` sem autorização. Nenhum segredo neste diretório: só
referências ao Secret `chat-local-secrets`, criado pelo operador.

## 0. Pré-requisitos do operador (fora do repo)

Banco Postgres dedicado externo (nunca a base do Authentik): base com dono
no usuário de serviço (`CREATE DATABASE chat_local OWNER db_admin`, via nó).
Imagem: construir no nó e publicar em `harbor.home.arpa/tese/chat-local:TAG`
(atual: `20260922-m6-4`). Pull secret `harbor-tese-pull` obrigatória no
namespace (containerd ignora o `docker login` do nó; template em
`deploy/templates/pull-secret.yaml`). DNS/TLS `chat.home.arpa` segue o padrão
`*.home.arpa` do Gateway.

## 1. Preflight (só leitura, desta máquina)

```bash
.venv/bin/python manage.py preflight --cluster --format json
```

Gate: `cluster_api/nodes/workloads/gateway` em `ok`.

## 2. Segredos (operador, fora do repo — nunca commitar)

```bash
kubectl -n chat-local create secret generic chat-local-secrets \
  --from-literal=DJANGO_SECRET_KEY='...' \
  --from-literal=PGHOST='...' \
  --from-literal=PGDATABASE='...' \
  --from-literal=PGUSER='...' \
  --from-literal=PGPASSWORD='...' \
  --from-literal=ANTHROPIC_API_KEY='...' \
  --from-literal=OIDC_ISSUER='https://authentik.home.arpa/application/o/.../' \
  --from-literal=OIDC_CLIENT_ID='...' \
  --from-literal=OIDC_CLIENT_SECRET='...' \
  --from-literal=OIDC_REDIRECT_URI='https://chat.home.arpa/oidc/callback'
```

Sem OIDC: omitir as 4 chaves `OIDC_*` (cai para login Django local).

## 3. Apply (autorizado, nesta ordem)

```bash
TAG=20260922-m6-4  # portátil BSD/Linux:
sed -i.bak "s|harbor.home.arpa/tese/chat-local:[^\"' ]*|harbor.home.arpa/tese/chat-local:${TAG}|g" deploy/deployment.yaml && rm -f deploy/deployment.yaml.bak
kubectl apply -f deploy/namespace.yaml
kubectl apply -f deploy/configmap.yaml
kubectl apply --dry-run=server -f deploy/  # valida sem criar
kubectl apply -f deploy/
```

## 4. Smoke

```bash
kubectl -n chat-local rollout status deploy/chat-local-web
kubectl -n chat-local get pods
curl -s https://chat.home.arpa/api/health
```

Gate M6: `rollout` completo + `/api/health` em `ok` + OIDC contra o
Authentik (quando habilitado) + matriz de degradação §21 demonstrada.
