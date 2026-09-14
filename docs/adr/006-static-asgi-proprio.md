# ADR-006 — Estáticos via handler ASGI próprio (WhiteNoise é WSGI-only)

Data: 2026-09-14 · Estado: aceita.

Contexto: o comando documentado (`uvicorn config.asgi:application`) precisa servir
HTML+CSS+JS sem `runserver`. WhiteNoise 6.12 foi avaliado e é WSGI-only
(`WhiteNoise.__call__` exige `start_response`; 500 em todas as rotas sob ASGI).
Decisão: `config/static_asgi.py` — wrapper ASGI mínimo servindo `/static/*` de
`STATIC_ROOT`, com trava anti-traversal (`resolve()` contido na raiz), 404 fora
da raiz e content-type por extensão.
Alternativas descartadas: WhiteNoise (WSGI-only na versão fixada); `runserver`
(não é o comando documentado); CDN (contraria RNF-01).
Consequências: sem dependência extra; verificado por `tests/integration/test_serve.py`
(uvicorn real) + smoke test com curl, incluindo tentativa de traversal → 404.
