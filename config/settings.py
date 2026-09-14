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
DEBUG = env("DJANGO_DEBUG", "true").lower() in ("1", "true", "yes")
ALLOWED_HOSTS = [
    h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()
]

INSTALLED_APPS = [
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
        "DIRS": [],
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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        # CHAT_DB_PATH: override documentado (ex.: teste de navegador isolado).
        "NAME": os.environ.get("CHAT_DB_PATH") or BASE_DIR / "data" / "chat.sqlite3",
        "TIMEOUT": 10,  # segundos; curto + retry explícito em vez de espera longa
        "OPTIONS": {"timeout": 10},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

CSRF_COOKIE_HTTPONLY = False  # o JS precisa ler o token para o fetch (padrão Django)
SESSION_COOKIE_SAMESITE = "Lax"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
