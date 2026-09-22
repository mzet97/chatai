# M6: imagem do perfil homelab (construída no nó k3s, sem Docker neste repo).
# RAG-E5 (2,1Gi em models/) fica FORA da imagem: baixa no runtime via
# `rag_prepare` ou volume dedicado. Smoke (/api/health) não precisa dele.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_DEBUG=false

WORKDIR /app

# libpq p/ psycopg (perfil postgres) + build mínimo p/ wheels fonte.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# torch CPU primeiro (evita o wheel CUDA de ~2,5Gi); o resto do pin mantido.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
    torch==2.14.0 && \
    pip install --no-cache-dir -r requirements.txt \
    "psycopg[binary]" && \
    pip check || true

COPY manage.py pyproject.toml ./
COPY config/ ./config/
COPY chat/ ./chat/

RUN python manage.py collectstatic --noinput

# data/ vem excluído do contexto (SQLite/RAG locais): recria vazio p/ o lite.
RUN mkdir -p /app/data/rag /app/staticfiles
RUN useradd -r -u 10000 app && chown -R app /app
USER app

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "config.asgi:application", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
