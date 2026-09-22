"""Helpers: corpo JSON das APIs (RNF: 4xx estável, nunca 500)."""

import json

from django.http import JsonResponse

TITLE_MAX = 200
MODEL_MAX = 200
SYSTEM_PROMPT_MAX = 8000


def parse_body(request):
    """Devolve (dict, None) ou (None, JsonResponse 400 com código estável)."""
    try:
        body = json.loads(request.body or "{}")
    except (ValueError, TypeError, UnicodeDecodeError):
        return None, JsonResponse(
            {"code": "validation", "message": "Corpo JSON inválido."}, status=400
        )
    if not isinstance(body, dict):
        return None, JsonResponse(
            {"code": "validation", "message": "Corpo JSON deve ser um objeto."}, status=400
        )
    return body, None


def clean_str(value, limit):
    """Texto da borda: só str, aparado no limite. None quando tipo inválido."""
    if not isinstance(value, str):
        return None
    return value[:limit]
