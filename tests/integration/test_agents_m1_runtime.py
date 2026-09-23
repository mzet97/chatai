"""M1: agente individual no runtime + precedência + sem regressão no Chat.

Sem chamadas pagas: SDK sempre simulado (tests/fakes.py). Sem commit.
"""

import json

import pytest
from asgiref.sync import sync_to_async

from chat.models import Conversation
from chat.models_agents import AgentDefinition
from chat.services import generation
from chat.services.agents import effective, profiles
from tests.fakes import (
    FakeClient,
    FakeFinalMessage,
    FakeMessagesNamespace,
    FakeStream,
    FakeStreamManager,
)

pytestmark = pytest.mark.django_db(transaction=True)
adb = sync_to_async


def _client_ok(text="ok"):
    final = FakeFinalMessage(text)
    mgr = FakeStreamManager(FakeStream([text], final))
    return FakeClient(FakeMessagesNamespace(stream_manager=mgr))


def _publish_geral(owner, model="claude-haiku-4-5-20251001", instructions="INSTRUCAO-DO-PERFIL"):
    profiles.ensure_examples(owner)
    geral = AgentDefinition.objects.get(owner=owner, name="Geral")
    v1 = geral.versions.get(revision=1)
    v1.model = model
    v1.task_instructions = instructions
    v1.save()
    profiles.publish_version(v1.pk, owner)
    v1.refresh_from_db()
    return geral, v1


async def _drain(run_id, user, client):
    return [e async for e in generation.execute_run(run_id, user=user, client=client)]


# --- precedência (AG-1.3/C-A2) ---


def test_chat_sem_agente_resolve_none(user, conversation):
    assert effective.resolve_agent(conversation) is None


def test_agente_aplica_versao_publicada(user, conversation):
    geral, v1 = _publish_geral(user)
    conversation.agent_mode = "agent"
    conversation.agent_definition = geral
    conversation.save()
    out = effective.resolve_agent(conversation)
    assert out["version"].revision == 1
    assert out["values"]["model"] == "claude-haiku-4-5-20251001"
    assert out["values"]["task_instructions"] == "INSTRUCAO-DO-PERFIL"
    assert out["origins"]["model"] == "agent_version"
    assert out["values"]["team_delegation"] == "not_applicable"


def test_override_da_conversa_vence_versao(user, conversation):
    geral, v1 = _publish_geral(user)
    conversation.agent_mode = "agent"
    conversation.agent_definition = geral
    conversation.preferred_model = "modelo-da-conversa"
    conversation.save()
    out = effective.resolve_agent(conversation)
    assert out["values"]["model"] == "modelo-da-conversa"
    assert out["origins"]["model"] == "conversation"


def test_sem_publicacao_arquivado_ou_sem_perfil_resolve_none(user, conversation):
    profiles.ensure_examples(user)
    geral = AgentDefinition.objects.get(owner=user, name="Geral")
    conversation.agent_mode = "agent"
    conversation.agent_definition = geral
    conversation.save()
    assert effective.resolve_agent(conversation) is None  # rascunho, sem publicar
    _publish_geral(user)
    assert effective.resolve_agent(conversation)["version"].revision == 1
    geral.archived = True
    geral.save()
    assert effective.resolve_agent(conversation) is None  # arquivado impede seleção
    geral.archived = False
    geral.save()
    conversation.agent_definition = None
    conversation.save()
    assert effective.resolve_agent(conversation) is None  # modo sem perfil
    conversation.agent_mode = "chat"
    conversation.save()
    assert effective.resolve_agent(conversation) is None


def test_team_marca_delegacao_indisponivel_m1(user, conversation):
    geral, v1 = _publish_geral(user)
    coord = AgentDefinition.objects.get(owner=user, name="Coordenador")
    cv = coord.versions.get(revision=1)
    cv.model = "m"
    cv.save()
    profiles.publish_version(cv.pk, user)
    conversation.agent_mode = "team"
    conversation.agent_definition = coord
    conversation.save()
    out = effective.resolve_agent(conversation)
    assert out["values"]["team_delegation"] == "unavailable_m1"
    assert out["origins"]["team_delegation"] == "policy"


# --- runtime de geração existente ---


async def test_agente_individual_entra_no_system_e_modelo(user, conversation):
    def _setup():
        geral, v1 = _publish_geral(user)
        conversation.agent_mode = "agent"
        conversation.agent_definition = geral
        conversation.save()
        return geral

    await adb(_setup)()
    conv = await adb(Conversation.objects.select_related("agent_definition").get)(
        pk=conversation.pk
    )
    run, _, _ = await adb(generation.reserve_run)(
        conversation=conv, content="oi", idempotency_key="k-ag"
    )
    client = _client_ok()
    events = await _drain(str(run.uuid), user, client)
    assert events[0]["type"] == "run_started"
    assert events[0]["model"] == "claude-haiku-4-5-20251001"
    (stream_kwargs,) = client.messages.calls["stream"]
    assert stream_kwargs["model"] == "claude-haiku-4-5-20251001"
    system_text = " ".join(b.get("text", "") for b in stream_kwargs["system"])
    assert "INSTRUCAO-DO-PERFIL" in system_text
    await adb(run.refresh_from_db)()
    assert run.snapshot["agent"]["applied"] is True
    assert run.snapshot["agent"]["definition"] == "Geral"
    assert run.snapshot["agent"]["version_revision"] == 1
    assert run.snapshot["agent"]["origins"]["model"] == "agent_version"


async def test_chat_nao_aplica_perfil_nem_delegacao(user, conversation):
    await adb(_publish_geral)(user)  # perfis existem, mas a conversa continua em chat
    run, _, _ = await adb(generation.reserve_run)(
        conversation=conversation, content="oi", idempotency_key="k-chat"
    )
    client = _client_ok()
    events = await _drain(str(run.uuid), user, client)
    assert events[0]["type"] == "run_started"
    (stream_kwargs,) = client.messages.calls["stream"]
    system_text = " ".join(b.get("text", "") for b in stream_kwargs["system"])
    assert "Perfil do agente" not in system_text
    await adb(run.refresh_from_db)()
    assert run.snapshot["agent"] == {"mode": "chat", "applied": False}
    assert "delegate_to_agent" not in json.dumps(stream_kwargs)


def test_pagina_agentes_renderiza(logged_client):
    r = logged_client.get("/agents/")
    assert r.status_code == 200
    assert "ag-examples" in r.content.decode()


def test_selecao_isolada_por_dono(client, user, django_user_model):
    """Perfil de outro dono não pode ser selecionado (400, sem vazar)."""
    other = django_user_model.objects.create_user("agboss", password="x")
    geral, _ = _publish_geral(other)
    conv = Conversation.objects.create(owner=user, title="t")
    client.force_login(user)
    r = client.patch(
        f"/api/conversations/{conv.uuid}",
        data=json.dumps({"agent_mode": "agent", "agent_definition_uuid": str(geral.uuid)}),
        content_type="application/json",
    )
    assert r.status_code == 400
    conv.refresh_from_db()
    assert conv.agent_mode == "chat" and conv.agent_definition_id is None
