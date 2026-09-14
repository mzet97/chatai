"""Critérios 1 e 2 (RF-02, RNF-01): persistência e isolamento por proprietário."""

import pytest

from chat.models import Conversation

pytestmark = pytest.mark.django_db


def test_crud_search_pagination_scoped(logged_client, user):
    for i in range(25):
        Conversation.objects.create(owner=user, title=f"chat {i:02d} sobre pythons")
    r = logged_client.get("/api/conversations?q=pythons")
    assert r.status_code == 200 and r.json()["count"] == 25
    r2 = logged_client.get("/api/conversations?q=pythons&page=2")
    assert r2.json()["page"] == 2 and len(r2.json()["results"]) == 5

    c = logged_client.post(
        "/api/conversations", data={"title": "t"}, content_type="application/json"
    ).json()
    r = logged_client.patch(
        f"/api/conversations/{c['uuid']}", data={"title": "novo"}, content_type="application/json"
    )
    assert r.json()["title"] == "novo"
    r = logged_client.patch(
        f"/api/conversations/{c['uuid']}", data={"archived": True}, content_type="application/json"
    )
    assert r.json()["archived"] is True
    assert logged_client.delete(f"/api/conversations/{c['uuid']}").status_code == 200
    assert Conversation.objects.filter(uuid=c["uuid"]).count() == 0


def test_cross_owner_blocked(user, user2, logged_client):
    from django.test import Client

    anon = Client()  # separado: logged_client loga o `client` compartilhado
    other = Conversation.objects.create(owner=user2, title="alheia")
    for url in (
        f"/api/conversations/{other.uuid}",
        f"/api/conversations/{other.uuid}/messages",
        f"/api/conversations/{other.uuid}/export",
    ):
        assert anon.get(url).status_code in (301, 302)  # sem login → login
    assert logged_client.get(f"/api/conversations/{other.uuid}").status_code == 404
    assert logged_client.get(f"/api/conversations/{other.uuid}/messages").status_code == 404
    assert logged_client.get(f"/api/conversations/{other.uuid}/export").status_code == 404


def test_first_message_sets_local_title(logged_client, conversation):
    r = logged_client.post(
        f"/api/conversations/{conversation.uuid}/messages",
        data={"content": "Explique fotossíntese em detalhes", "idempotency_key": "k1"},
        content_type="application/json",
    )
    assert r.status_code == 201
    conversation.refresh_from_db()
    assert conversation.title.startswith("Explique fotossíntese")
