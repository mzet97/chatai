"""ASGI. Handler próprio serve /static/* para que o comando uvicorn documentado
sirva HTML+CSS+JS sem runserver (WhiteNoise é WSGI-only)."""

import os

import django
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.conf import settings  # noqa: E402

from config.static_asgi import static_wrapper  # noqa: E402

application = static_wrapper(get_asgi_application(), settings.STATIC_ROOT)
