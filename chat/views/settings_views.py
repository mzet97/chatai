"""Configurações (RF-05), diagnóstico (RF-06) e catálogo (RF-07)."""

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from chat.models import ConnectionSettings
from chat.services import configuration as cfg
from chat.services.anthropic_client import classify_error, resolve_for_user
from chat.services.diagnostics import diagnose, diagnose_generation
from chat.services.model_catalog import (
    get_cache,
    invalidate_stale_caches,
    profile_for,
    refresh_catalog,
    resolve_model,
)
from chat.services.providers import get_provider
from chat.views._body import parse_body

# Seam de teste (como em api_runs): produção usa o SDK real.
CLIENT_FACTORY = None


def _ui(user) -> ConnectionSettings:
    ui, _ = ConnectionSettings.objects.get_or_create(owner=user)
    return ui


@login_required
@require_http_methods(["GET"])
def settings_api(request):
    ui = _ui(request.user)
    resolved, cred = resolve_for_user(request.user)
    data = resolved.as_dict()
    data.update(
        {
            "keychain_ref": ui.keychain_ref,
            "keychain_available": cfg.keyring_available(),
            "revision": ui.revision,
        }
    )
    return JsonResponse(data)


@login_required
@require_http_methods(["POST"])
def settings_save(request):
    """Campo de escrita para a chave: vazio = manter; remoção em ação própria."""
    body, err = parse_body(request)
    if err is not None:
        return err
    ui = _ui(request.user)

    new_base = (body.get("base_url") or "").strip()
    if new_base and new_base != (ui.base_url or ""):
        # Troca de domínio exige confirmação explícita.
        if not body.get("confirm_endpoint_change"):
            return JsonResponse(
                {
                    "code": "validation",
                    "message": "Troca de endpoint exige confirmação explícita.",
                    "needs_confirmation": True,
                },
                status=400,
            )
        try:
            cfg.validate_base_url(new_base)
        except ValueError as exc:
            return JsonResponse({"code": "validation", "message": str(exc)}, status=400)
        ui.base_url = new_base

    for field in ("default_model",):
        if field in body:
            setattr(ui, field, (body[field] or "").strip())
    for field in ("timeout_seconds", "max_retries"):
        if field in body and body[field] not in (None, ""):
            try:
                setattr(ui, field, max(0, int(body[field])))
            except (TypeError, ValueError):
                return JsonResponse(
                    {"code": "validation", "message": f"{field} deve ser inteiro."}, status=400
                )

    use_keychain = bool(body.get("use_keychain"))
    new_key = body.get("api_key")  # None = ausente; "" = manter
    if new_key:
        if use_keychain:
            if not cfg.keyring_available():
                return JsonResponse(
                    {
                        "code": "validation",
                        "message": "Keychain indisponível: use .env/ambiente. "
                        "Nunca gravamos a chave em texto puro no SQLite.",
                    },
                    status=400,
                )
            cfg.write_keychain(new_key)
            ui.keychain_ref = "keyring:claude-chat-local/api-key"
        else:
            return JsonResponse(
                {
                    "code": "validation",
                    "message": "Sem Keychain selecionado, configure a chave via .env ou ambiente. "
                    "Não armazenamos chave em texto puro.",
                },
                status=400,
            )
    elif use_keychain != bool(ui.keychain_ref):
        ui.keychain_ref = "keyring:claude-chat-local/api-key" if use_keychain else ""
    ui.key_origin = "keychain" if ui.keychain_ref else (ui.key_origin or "")
    ui.revision += 1
    ui.save()
    return JsonResponse(
        {"saved": True, "revision": ui.revision, "keychain_available": cfg.keyring_available()}
    )


@login_required
@require_http_methods(["POST"])
def settings_remove_key(request):
    """Remoção da chave: ação própria, explícita."""
    ui = _ui(request.user)
    if ui.keychain_ref:
        cfg.delete_keychain()
        ui.keychain_ref = ""
    ui.key_origin = ""
    ui.revision += 1
    ui.save()
    return JsonResponse({"removed": True})


@login_required
@require_http_methods(["POST"])
async def settings_diagnose(request):
    from asgiref.sync import sync_to_async

    body, err = parse_body(request)
    if err is not None:
        return err
    resolved, cred = await sync_to_async(resolve_for_user)(request.user)
    client = CLIENT_FACTORY(request.user) if CLIENT_FACTORY else None
    steps = await _run_diagnose(resolved, cred, client)
    include_generation = bool(
        body.get("include_generation")
    )  # autorização explícita (custa tokens)
    generation = None
    if include_generation:
        if client is None:
            client = get_provider().build_client(
                api_key=cred.secret,
                base_url=resolved.base_url,
                timeout_seconds=resolved.timeout_seconds,
                max_retries=resolved.max_retries,
            )
        generation = await diagnose_generation(client=client, model=resolved.model)
    return JsonResponse({"steps": steps, "generation": generation})


async def _run_diagnose(resolved, cred, client):
    return await diagnose(
        secret=cred.secret,
        base_url=resolved.base_url,
        timeout=resolved.timeout_seconds,
        retries=resolved.max_retries,
        client=client,
    )


@login_required
@require_http_methods(["GET", "POST"])
async def models_api(request):
    """GET: catálogo (cache + disponibilidade do candidato). POST: atualização manual."""
    from asgiref.sync import sync_to_async

    resolved, cred = await sync_to_async(resolve_for_user)(request.user)
    if not cred.secret:
        return JsonResponse(
            {
                "code": "unauthorized",
                "message": "Nenhuma chave configurada.",
                "models": [],
                "revalidated": False,
            },
            status=401,
        )
    profile = profile_for(cred.secret, resolved.base_url)
    if request.method == "POST":
        client = CLIENT_FACTORY(request.user) if CLIENT_FACTORY else None
        if client is None:
            client = get_provider().build_client(
                api_key=cred.secret,
                base_url=resolved.base_url,
                timeout_seconds=resolved.timeout_seconds,
                max_retries=resolved.max_retries,
            )
        try:
            models, _ = await refresh_catalog(
                client=client, secret=cred.secret, base_url=resolved.base_url
            )
        except Exception as exc:
            code, message = classify_error(exc)
            cached = await sync_to_async(get_cache)(profile)
            return JsonResponse(
                {
                    "code": code,
                    "message": message,
                    "models": cached.payload if cached else [],
                    "revalidated": False,
                    "stale": True,
                },
                status=502,
            )
        await sync_to_async(invalidate_stale_caches)(keep_profile=profile)
        return JsonResponse({"models": models, "revalidated": True, "stale": False})
    cached = await sync_to_async(get_cache)(profile)
    if cached is None:
        return JsonResponse(
            {"models": [], "revalidated": False, "message": "Sem cache: use atualizar."}
        )
    model, revalidated, warning = resolve_model(
        candidate=resolved.model,
        available_ids=[m["id"] for m in cached.payload],
        fallback=resolved.model,
    )
    return JsonResponse(
        {
            "models": cached.payload,
            "revalidated": True,
            "fetched_at": cached.fetched_at.isoformat(),
            "candidate": model,
            "warning": warning,
        }
    )
