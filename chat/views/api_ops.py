"""M6: health público para probes (§20). Sem auth, sem segredos, sem paid calls."""

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def health(request):
    import os

    try:
        connection.ensure_connection()
        db_ok = True
    except Exception:
        db_ok = False
    from chat.services.identity import oidc_config

    degraded = not db_ok
    return JsonResponse(
        {
            "status": "degraded" if degraded else "ok",
            "database": "ok" if db_ok else "degraded",
            "anthropic_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "oidc_enabled": oidc_config()["enabled"],
        }
    )
