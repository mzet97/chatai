"""M1: API de agentes — CRUD, versões, exemplos e seleção por conversa."""

import json

import pytest

from chat.models_agents import AgentDefinition

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_user("agapi", password="x")


def _post(client, path, body=None):
    return client.post(path, data=json.dumps(body or {}), content_type="application/json")


def test_crud_e_isolamento(client, owner, django_user_model):
    client.force_login(owner)
    assert _post(client, "/api/agents", {"name": ""}).status_code == 400
    r = _post(client, "/api/agents", {"name": "Geral", "kind": "general"})
    assert r.status_code == 201
    uuid = r.json()["uuid"]
    assert _post(client, "/api/agents", {"name": "Geral"}).status_code == 400
    assert client.get("/api/agents").json()["results"][0]["name"] == "Geral"
    other = django_user_model.objects.create_user("agapi2", password="x")
    client.force_login(other)
    assert client.get(f"/api/agents/{uuid}").status_code == 404
    assert client.get("/api/agents").json()["results"] == []


def test_duplicar_arquivar_e_excluir(client, owner):
    client.force_login(owner)
    uuid = _post(client, "/api/agents", {"name": "Base"}).json()["uuid"]
    dup = _post(client, f"/api/agents/{uuid}/duplicate")
    assert dup.status_code == 201 and dup.json()["name"].startswith("Base (cópia)")
    assert (
        client.patch(
            f"/api/agents/{uuid}",
            data=json.dumps({"archived": True}),
            content_type="application/json",
        ).status_code
        == 200
    )
    assert [d["name"] for d in client.get("/api/agents").json()["results"]] == ["Base (cópia)"]
    assert client.delete(f"/api/agents/{dup.json()['uuid']}").status_code == 200


def test_versao_imutavel_e_publicacao(client, owner):
    client.force_login(owner)
    uuid = _post(client, "/api/agents", {"name": "Rev"}).json()["uuid"]
    v1 = client.get(f"/api/agents/{uuid}").json()["versions"][0]["uuid"]
    assert _post(client, f"/api/agent-versions/{v1}/publish").status_code == 400
    assert (
        client.patch(
            f"/api/agent-versions/{v1}",
            data=json.dumps({"model": "m", "task_instructions": "t"}),
            content_type="application/json",
        ).status_code
        == 200
    )
    assert _post(client, f"/api/agent-versions/{v1}/publish").status_code == 200
    bad = client.patch(
        f"/api/agent-versions/{v1}",
        data=json.dumps({"model": "outro"}),
        content_type="application/json",
    )
    assert bad.status_code == 409
    draft = _post(client, f"/api/agents/{uuid}/drafts")
    assert draft.status_code == 201 and draft.json()["revision"] == 2


def test_examples_e_selecao_na_conversa(client, owner):
    from chat.models import Conversation

    client.force_login(owner)
    assert _post(client, "/api/agents/examples").json()["total"] == 4
    coord = AgentDefinition.objects.get(owner=owner, name="Coordenador")
    conv = Conversation.objects.create(owner=owner, title="t")
    bad = client.patch(
        f"/api/conversations/{conv.uuid}",
        data=json.dumps({"agent_mode": "team"}),
        content_type="application/json",
    )
    assert bad.status_code == 400
    ok = client.patch(
        f"/api/conversations/{conv.uuid}",
        data=json.dumps({"agent_mode": "team", "agent_definition_uuid": str(coord.uuid)}),
        content_type="application/json",
    )
    assert ok.status_code == 200
    assert ok.json()["agent_mode"] == "team"
    geral = AgentDefinition.objects.get(owner=owner, name="Geral")
    team_com_geral = client.patch(
        f"/api/conversations/{conv.uuid}",
        data=json.dumps({"agent_mode": "team", "agent_definition_uuid": str(geral.uuid)}),
        content_type="application/json",
    )
    assert team_com_geral.status_code == 400
