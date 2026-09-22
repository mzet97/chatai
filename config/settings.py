"""Settings. O .env é resolvido pela RAIZ do projeto (não pelo cwd)."""

import os
from pathlib import Path

from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent.parent

# Leitura explícita: ambiente do processo vence o .env; nada é escrito em os.environ.
# CHAT_DOTENV_PATH: override documentado (ex.: teste de navegador hermético).
_file_env = dotenv_values(os.environ.get("CHAT_DOTENV_PATH") or BASE_DIR / ".env")


def env(name: str, default: str = "") -> str:
    import os

    return os.environ.get(name, _file_env.get(name, default))


SECRET_KEY = env("DJANGO_SECRET_KEY", "django-insecure-local-dev-only-change-me")

# Provedor de geração (Rev1.1 §11): Anthropic obrigatório e padrão.
# OPENAI_ENABLED existe só como trava documentada do backlog futuro —
# nenhum código desta entrega a consulta para habilitar chamadas.
AI_PROVIDER = env("AI_PROVIDER", "anthropic")
OPENAI_ENABLED = env("OPENAI_ENABLED", "false").lower() in ("1", "true", "yes")
DEBUG = env("DJANGO_DEBUG", "true").lower() in ("1", "true", "yes")
ALLOWED_HOSTS = [
    h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "chat",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Primeiro: overrides locais (ex.: skin do admin) vencem os templates
        # do django.contrib.admin, listado antes de "chat" no INSTALLED_APPS.
        "DIRS": [BASE_DIR / "chat" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
ASGI_APPLICATION = "config.asgi.application"

def _databases():
    """Seleção de banco por perfil (M3/ADR-5): sqlite (local-lite, padrão) ou
    postgres (homelab, dedicado — nunca a base do Authentik)."""
    engine = env("DB_ENGINE", "sqlite").strip().lower()
    if engine == "sqlite":
        return {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                # CHAT_DB_PATH: override documentado (ex.: teste de navegador isolado).
                "NAME": os.environ.get("CHAT_DB_PATH") or BASE_DIR / "data" / "chat.sqlite3",
                "TIMEOUT": 10,  # segundos; curto + retry explícito em vez de espera longa
                "OPTIONS": {"timeout": 10},
            }
        }
    if engine == "postgres":
        live_secret = env("DJANGO_SECRET_KEY", "django-insecure-local-dev-only-change-me")
        live_debug = env("DJANGO_DEBUG", "true").lower() in ("1", "true", "yes")
        if live_secret.startswith("django-insecure-"):
            raise RuntimeError(
                "DB_ENGINE=postgres exige DJANGO_SECRET_KEY própria; "
                "o valor padrão de desenvolvimento é só para sqlite local."
            )
        if live_debug:
            raise RuntimeError("DB_ENGINE=postgres exige DJANGO_DEBUG=false.")
        try:
            import psycopg  # noqa: F401 — driver do perfil homelab
        except ImportError:
            raise RuntimeError(
                "DB_ENGINE=postgres exige o driver 'psycopg' instalado; "
                "o perfil local-lite (sqlite) continua o padrão. "
                "Sem fallback silencioso para outro banco."
            ) from None
        missing = [v for v in ("PGHOST", "PGDATABASE", "PGUSER") if not env(v)]
        if missing:
            raise RuntimeError(
                f"DB_ENGINE=postgres sem configuração: faltam {', '.join(missing)} "
                "(PGHOST/PGDATABASE/PGUSER + PGPASSWORD via segredo montado)."
            )
        return {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "HOST": env("PGHOST"),
                "PORT": env("PGPORT", "5432"),
                "NAME": env("PGDATABASE"),
                "USER": env("PGUSER"),
                "PASSWORD": env("PGPASSWORD"),
                "CONN_MAX_AGE": 60,
                "OPTIONS": {"sslmode": env("PGSSLMODE", "prefer")},
            }
        }
    raise RuntimeError(
        f"DB_ENGINE={engine!r} desconhecido: use 'sqlite' (local-lite) ou "
        "'postgres' (homelab com banco dedicado)."
    )


DATABASES = _databases()

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# CHAT_RAG_DIR: override documentado (ex.: teste de navegador isolado).
RAG_STORAGE_DIR = os.environ.get("CHAT_RAG_DIR") or BASE_DIR / "data" / "rag"
STORAGES = {
    "default": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

CSRF_COOKIE_HTTPONLY = False  # o JS precisa ler o token para o fetch (padrão Django)
SESSION_COOKIE_SAMESITE = "Lax"
# M6/homelab: atrás do Gateway com terminação TLS, o POST chega em http
# interno com Origin https — confia nas origens derivadas de ALLOWED_HOSTS.
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h not in ("", "*")]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
