"""Fase 1 do plano: corpo inválido → 400 estável, nunca 500; auth consistente."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from chat.models import Conversation

pytestmark = pytest.mark.integration


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("fase1", password="pw123456")


@pytest.fixture
def authed(user):
    c = Client()
    assert c.login(username="fase1", password="pw123456")
    return c


@pytest.fixture
def conv(user):
    return Conversation.objects.create(owner=user, title="t")


BAD = "{não json"


@pytest.mark.parametrize("url_kind", ["create", "runs", "settings"])
def test_corpo_invalido_400(authed, conv, url_kind):
    url = {
        "create": "/api/conversations",
        "runs": f"/api/conversations/{conv.uuid}/runs",
        "settings": "/api/settings/save",
    }[url_kind]
    r = authed.post(url, data=BAD, content_type="application/json")
    assert r.status_code == 400
    assert r.json()["code"] == "validation"


def test_patch_conversa_corpo_invalido(authed, conv):
    r = authed.patch(
        f"/api/conversations/{conv.uuid}",
        data=BAD,
        content_type="application/json",
    )
    assert r.status_code == 400
    assert r.json()["code"] == "validation"


def test_patch_conversa_tipos_invalidos(authed, conv):
    r = authed.patch(
        f"/api/conversations/{conv.uuid}",
        data={"title": 123, "preferred_model": ["x"]},
        content_type="application/json",
    )
    assert r.status_code == 400
    assert r.json()["code"] == "validation"
    conv.refresh_from_db()
    assert conv.title == "t"


def test_patch_conversa_apara_limites(authed, conv):
    r = authed.patch(
        f"/api/conversations/{conv.uuid}",
        data={"title": "y" * 500},
        content_type="application/json",
    )
    assert r.status_code == 200
    conv.refresh_from_db()
    assert len(conv.title) == 200


def test_corpo_nao_objeto_400(authed):
    r = authed.post("/api/conversations", data="[1,2]", content_type="application/json")
    assert r.status_code == 400
    assert r.json()["code"] == "validation"


def test_settings_page_exige_login():
    r = Client().get("/settings/")
    assert r.status_code == 302
    assert "/login/" in r["Location"]


def test_resolve_int_invalido_cai_no_padrao():
    from chat.services.configuration import DEFAULTS, resolve_int

    value, origin = resolve_int("CHAT_API_TIMEOUT_SECONDS", conversation_value="abc")
    assert value == int(DEFAULTS["CHAT_API_TIMEOUT_SECONDS"])
    assert origin == "default"


def test_base_url_recusa_credencial_em_localhost():
    import pytest as _pytest

    from chat.services.configuration import validate_base_url

    with _pytest.raises(ValueError):
        validate_base_url("https://user:pass@127.0.0.1:8443/x")
    assert validate_base_url("https://127.0.0.1:8443/x") == "https://127.0.0.1:8443/x"
