"""Catálogo de modelos via Models API paginada + cache (RF-07)."""

from __future__ import annotations

from chat.models import ModelCatalogCache
from chat.services import configuration as cfg


def profile_for(secret: str, base_url: str) -> str:
    return f"{cfg.key_fingerprint(secret)}@{base_url}"


async def fetch_all_models(client) -> list[dict]:
    """Percorre a paginação completa (after_id). Sem lista manual fixa."""
    models, after_id = [], None
    while True:
        page = (
            await client.models.list(after_id=after_id) if after_id else await client.models.list()
        )
        for m in page.data:
            models.append(
                {
                    "id": m.id,
                    "display_name": getattr(m, "display_name", None) or m.id,
                    "created_at": str(getattr(m, "created_at", "") or ""),
                    "type": getattr(m, "type", "") or "",
                }
            )
        if not getattr(page, "has_more", False) or not page.data:
            break
        after_id = page.data[-1].id
    return models


def get_cache(profile: str) -> ModelCatalogCache | None:
    return ModelCatalogCache.objects.filter(profile=profile).first()


async def refresh_catalog(*, client, secret: str, base_url: str) -> tuple[list[dict], bool]:
    """Retorna (modelos, from_cache). Em falha, preserva cache/identificador anterior."""
    from asgiref.sync import sync_to_async

    profile = profile_for(secret, base_url)
    try:
        models = await fetch_all_models(client)
    except Exception:
        cached = await sync_to_async(get_cache)(profile)
        if cached is not None:
            return cached.payload, True
        raise
    await sync_to_async(ModelCatalogCache.objects.update_or_create)(
        profile=profile, defaults={"endpoint": base_url, "payload": models}
    )
    return models, False


def invalidate_stale_caches(*, keep_profile: str) -> int:
    """Invalida caches de outro perfil/endpoint. Retorna nº removido."""
    return ModelCatalogCache.objects.exclude(profile=keep_profile).delete()[0]


def resolve_model(
    *, candidate: str, available_ids: list[str] | None, fallback: str
) -> tuple[str, bool, str]:
    """Retorna (modelo, revalidado, aviso). Nunca substitui silenciosamente."""
    if available_ids is None:
        return candidate, False, "Catálogo indisponível: identificador preservado, não revalidado."
    if candidate in available_ids:
        return candidate, True, ""
    return (
        candidate,
        False,
        (
            f"'{candidate}' não está no catálogo atual. Escolha entre os modelos retornados; "
            f"nenhuma substituição automática foi feita."
        ),
    )
