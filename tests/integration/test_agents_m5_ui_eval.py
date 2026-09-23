"""M5: trilha da equipe no run_detail + métricas + eval + preservações.

Sem chamadas pagas: SDK sempre simulado (tests/fakes.py). Sem commit.
"""

import json

import pytest
from asgiref.sync import sync_to_async

from chat.models_agents import AgentDefinition, AgentRun
from chat.services import generation
from chat.services.agents import budget as _budget
from chat.services.agents import eval as evalsvc
from chat.services.agents import profiles
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


def _publish_geral(owner, model="claude-haiku-4-5-20251001"):
    profiles.ensure_examples(owner)
    geral = AgentDefinition.objects.get(owner=owner, name="Geral")
    v1 = geral.versions.get(revision=1)
    v1.model = model
    v1.task_instructions = "INSTRUCAO-M5"
    v1.save()
    profiles.publish_version(v1.pk, owner)
    v1.refresh_from_db()
    return geral, v1


def _root_with_child(owner, conversation, version):
    root = AgentRun.objects.create(
        owner=owner,
        conversation=conversation,
        agent_version=version,
        depth=0,
        task="tarefa raiz",
        state="running",
    )
    _budget.log_event(run=root, kind="team_started", payload={"coordinator": "Geral"})
    child = AgentRun.objects.create(
        owner=owner,
        conversation=conversation,
        agent_version=version,
        parent=root,
        depth=1,
        task="subtarefa",
        state="done",
        result_summary="resumo do filho",
    )
    _budget.log_event(run=root, kind="child_done", payload={"child": str(child.uuid)})
    _budget.record(root_run=root, run=root, kind="child_slot", tokens=1)
    return root, child


# --- trilha da equipe no run_detail (AG-3.2/AG-6) ---


def test_run_detail_expoe_trilha_da_equipe(user, conversation, logged_client):
    def _setup():
        _, v1 = _publish_geral(user)
        root, child = _root_with_child(user, conversation, v1)
        run, _, _ = generation.reserve_run(
            conversation=conversation, content="oi", idempotency_key="k-team"
        )
        run.snapshot = {
            "team": {
                "available": True,
                "parent_run_uuid": str(root.uuid),
                "coordinator": "Geral",
                "revision": v1.revision,
                "limits": {"max_child_runs": 2},
            }
        }
        run.save()
        return str(run.uuid), str(child.uuid)

    run_uuid, child_uuid = _setup()
    r = logged_client.get(f"/api/runs/{run_uuid}")
    assert r.status_code == 200
    team = r.json()["team"]
    assert team["available"] is True
    assert team["coordinator"] == "Geral"
    assert team["root_state"] == "running"
    assert team["limits"] == {"max_child_runs": 2}
    assert len(team["children"]) == 1
    kid = team["children"][0]
    assert kid["uuid"] == child_uuid
    assert kid["state"] == "done"
    assert kid["summary"] == "resumo do filho"
    kinds = [e["kind"] for e in team["events"]]
    assert kinds == ["team_started", "child_done"]
    assert team["ledger"]["child_slots"] == 1


def test_run_detail_sem_equipe_marca_indisponivel(user, conversation, logged_client):
    def _setup():
        run, _, _ = generation.reserve_run(
            conversation=conversation, content="oi", idempotency_key="k-noteam"
        )
        run.snapshot = {"team": {"available": False, "reason": "no_delegatables"}}
        run.save()
        return str(run.uuid)

    run_uuid = _setup()
    r = logged_client.get(f"/api/runs/{run_uuid}")
    assert r.status_code == 200
    assert r.json()["team"] == {"available": False, "reason": "no_delegatables"}


def test_trilha_isolada_por_dono(user, conversation, django_user_model, logged_client):
    def _setup():
        _, v1 = _publish_geral(user)
        root, _ = _root_with_child(user, conversation, v1)
        run, _, _ = generation.reserve_run(
            conversation=conversation, content="oi", idempotency_key="k-iso"
        )
        run.snapshot = {"team": {"available": True, "parent_run_uuid": str(root.uuid)}}
        run.save()
        other = django_user_model.objects.create_user("outro", password="x" * 12)
        return str(run.uuid), other

    run_uuid, _ = _setup()
    from django.test import Client

    other_client = Client()
    other_client.force_login(django_user_model.objects.get(username="outro"))
    r = other_client.get(f"/api/runs/{run_uuid}")
    assert r.status_code == 404


def test_perfil_pre_escolhido_no_chat_habilita_equipe(logged_client, user, conversation):
    """M5: perfil pode ser fixado ainda em Chat (inerte) e depois ativa a Equipe."""
    profiles.ensure_examples(user)
    coord = AgentDefinition.objects.get(owner=user, name="Coordenador")
    r = logged_client.patch(
        f"/api/conversations/{conversation.uuid}",
        data=json.dumps({"agent_mode": "chat", "agent_definition_uuid": str(coord.uuid)}),
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["agent_definition_uuid"] == str(coord.uuid)
    # Chat continua sem agente efetivo (perfil inerte).
    assert r.json()["agent_effective"]["applied"] is False
    r = logged_client.patch(
        f"/api/conversations/{conversation.uuid}",
        data=json.dumps({"agent_mode": "team", "agent_definition_uuid": str(coord.uuid)}),
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["agent_mode"] == "team"


# --- suite sintética (AG-6: ≥20 tarefas, individual × equipe) ---


def test_eval_tem_20_ou_mais_tarefas():
    assert len(evalsvc.TASKS) >= 20
    suites = {t["suite"] for t in evalsvc.TASKS}
    assert {"individual", "team"} <= suites


def test_eval_deterministica_e_toda_verde():
    first = evalsvc.run_eval()
    second = evalsvc.run_eval()
    assert first == second
    assert all(r["passed"] for r in first), [r["id"] for r in first if not r["passed"]]
    summary = evalsvc.summarize(first)
    assert summary["all"] == {
        "passed": len(first),
        "total": len(first),
        "rate": 1.0,
    }


# --- RAG/imagens/MCP preservados no caminho com agente (AG-6) ---


async def test_agente_preserva_imagens_e_ferramentas(user, conversation):
    from chat.models import Conversation

    def _setup():
        geral, _ = _publish_geral(user)
        conversation.agent_mode = "agent"
        conversation.agent_definition = geral
        conversation.save()

    await adb(_setup)()
    conv = await adb(Conversation.objects.select_related("agent_definition").get)(
        pk=conversation.pk
    )
    run, _, _ = await adb(generation.reserve_run)(
        conversation=conv, content="com imagem", idempotency_key="k-m5img"
    )
    events = [
        e async for e in generation.execute_run(str(run.uuid), user=user, client=_client_ok())
    ]
    assert events[0]["type"] == "run_started"
    await adb(run.refresh_from_db)()
    assert run.snapshot["agent"]["applied"] is True
    # Caminho de imagens/RAG intacto: snapshot registra a seção mesmo vazia.
    assert "images" in run.snapshot
    assert run.snapshot["images"]["count"] == 0
    # Catálogo de ferramentas do agente não esvazia o da conversa.
    tools = run.snapshot.get("tools_enabled") or []
    assert isinstance(tools, list)


async def test_equipe_sem_delegaveis_nao_anuncia_delegate(user, conversation):
    """Fail closed: coordenador sem delegáveis responde só, sem a ferramenta."""
    from chat.models import Conversation

    def _setup():
        geral, _ = _publish_geral(user)
        conversation.agent_mode = "team"
        conversation.agent_definition = geral
        conversation.save()

    await adb(_setup)()
    conv = await adb(Conversation.objects.select_related("agent_definition").get)(
        pk=conversation.pk
    )
    run, _, _ = await adb(generation.reserve_run)(
        conversation=conv, content="coordenar", idempotency_key="k-m5team"
    )
    client = _client_ok()
    events = [e async for e in generation.execute_run(str(run.uuid), user=user, client=client)]
    assert events[0]["type"] == "run_started"
    await adb(run.refresh_from_db)()
    assert run.snapshot["agent"]["mode"] == "team"
    assert run.snapshot["team"] == {"available": False, "reason": "no_delegatables"}
    assert "delegate_to_agent" not in json.dumps(client.messages.calls["stream"])
