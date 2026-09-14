"""Critérios 6 e 7 (RF-06/RF-07): diagnóstico em 4 passos e catálogo paginado."""

import httpx2 as httpx
import pytest
from anthropic import AuthenticationError
from asgiref.sync import sync_to_async

from chat.models import ModelCatalogCache
from chat.services.diagnostics import diagnose, diagnose_generation
from chat.services.model_catalog import (
    invalidate_stale_caches,
    refresh_catalog,
    resolve_model,
)
from tests.fakes import (
    FakeClient,
    FakeFinalMessage,
    FakeMessagesNamespace,
    FakeModelsNamespace,
    model_item,
)

pytestmark = pytest.mark.django_db(transaction=True)


def _resp(status):
    return httpx.Response(status, request=httpx.Request("POST", "https://x.test"))


async def test_diagnose_steps_distinguished():
    ok_models = FakeModelsNamespace(
        pages=[{"after": None, "items": [model_item("m1")], "has_more": False}]
    )
    steps = await diagnose(
        secret="sk",
        base_url="https://api.anthropic.com",
        timeout=10,
        retries=0,
        client=FakeClient(models=ok_models),
    )
    by_id = {s["id"]: s for s in steps}
    assert by_id["config"]["ok"] and "não é autenticação" in by_id["config"]["detail"].lower()
    assert by_id["client"]["ok"]
    assert by_id["auth"]["ok"] and "não prova" in by_id["auth"]["detail"]

    bad = FakeModelsNamespace(error=AuthenticationError("x", response=_resp(401), body={}))
    steps = await diagnose(
        secret="sk",
        base_url="https://api.anthropic.com",
        timeout=10,
        retries=0,
        client=FakeClient(models=bad),
    )
    assert steps[-1] == {
        "id": "auth",
        "ok": False,
        "code": "unauthorized",
        "detail": steps[-1]["detail"],
    }

    steps = await diagnose(
        secret=None,
        base_url="https://api.anthropic.com",
        timeout=10,
        retries=0,
        client=FakeClient(),
    )
    assert steps == [{"id": "config", "ok": False, "detail": "Nenhuma chave configurada."}]


async def test_generation_probe_is_explicit_and_small():
    final = FakeFinalMessage("ok")
    msgs = FakeMessagesNamespace(create_result=final)
    out = await diagnose_generation(client=FakeClient(messages=msgs), model="m-x")
    assert out["ok"] and out["text"] == "ok"
    assert msgs.calls["create"][0]["max_tokens"] == 16  # pequeno por construção


async def test_catalog_pagination_and_invalidation():
    pages = [
        {"after": None, "items": [model_item("m1"), model_item("m2")], "has_more": True},
        {"after": "m2", "items": [model_item("m3")], "has_more": False},
    ]
    client = FakeClient(models=FakeModelsNamespace(pages=pages))
    models, from_cache = await refresh_catalog(
        client=client, secret="s1", base_url="https://api.anthropic.com"
    )
    assert [m["id"] for m in models] == ["m1", "m2", "m3"]  # paginação completa
    assert client.models.calls == [None, "m2"]  # cursor after_id percorrido
    assert from_cache is False

    # Falha posterior preserva cache/identificador (nunca fabrica sucesso).
    down = FakeClient(models=FakeModelsNamespace(error=RuntimeError("rede")))
    models2, from_cache2 = await refresh_catalog(
        client=down, secret="s1", base_url="https://api.anthropic.com"
    )
    assert (from_cache2, [m["id"] for m in models2]) == (True, ["m1", "m2", "m3"])

    # Troca de perfil/endpoint invalida o cache antigo.
    removed = await sync_to_async(invalidate_stale_caches)(keep_profile="outro-perfil")
    count = await sync_to_async(ModelCatalogCache.objects.count)()
    assert removed == 1 and count == 0


def test_resolve_model_never_silently_substitutes():
    model, ok, warn = resolve_model(candidate="novo-x", available_ids=["a", "b"], fallback="a")
    assert model == "novo-x" and ok is False and "nenhuma substituição" in warn.lower()
    model, ok, _ = resolve_model(candidate="a", available_ids=["a", "b"], fallback="a")
    assert (model, ok) == ("a", True)
    model, ok, warn = resolve_model(candidate="a", available_ids=None, fallback="a")
    assert (model, ok) == ("a", False) and "não revalidado" in warn
