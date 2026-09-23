"""M4: Equipe — delegate_to_agent, filhas, orçamento, cancelamento, retomada.

Sem chamadas pagas: SDK sempre simulado (tests/fakes.py). Sem commit.
"""

import asyncio

import pytest
from asgiref.sync import sync_to_async

from chat.models import Conversation
from chat.models_agents import AgentDefinition, AgentRun, BudgetLedger, Delegation
from chat.services import generation
from chat.services.agents import budget, profiles, team
from chat.services.tools import executor as _executor
from chat.services.tools.local_tools import all_records
from tests.fakes import (
    FakeClient,
    FakeFinalMessage,
    FakeMessagesNamespace,
    FakeToolMessage,
    FakeToolUseBlock,
)

pytestmark = pytest.mark.django_db(transaction=True)
adb = sync_to_async


def _publish(owner, name, model="claude-haiku-4-5-20251001"):
    profiles.ensure_examples(owner)
    definition = AgentDefinition.objects.get(owner=owner, name=name)
    version = definition.versions.get(revision=1)
    version.model = model
    version.save()
    profiles.publish_version(version.pk, owner)
    version.refresh_from_db()
    return definition, version


def _setup_team(user):
    _publish(user, "Pesquisador")
    _publish(user, "Revisor")
    coord_def, coord_v = _publish(user, "Coordenador")
    pesq_def = AgentDefinition.objects.get(owner=user, name="Pesquisador")
    return coord_def, coord_v, pesq_def


def _root(user, conversation, version):
    return AgentRun.objects.create(
        owner=user, conversation=conversation, agent_version=version, depth=0, state="running"
    )


def _hook(user, conversation, root, version, runner, catalog=None):
    def resolve(uuid_str):
        from chat.models_agents import AgentDefinition as _D

        try:
            definition = _D.objects.get(uuid=uuid_str, owner_id=user.pk)
        except Exception:
            return None
        for cand in definition.versions.order_by("-revision"):
            if cand.published and profiles.is_complete(cand):
                return cand
        return None

    return team.build_handler(
        owner=user,
        conversation=conversation,
        parent_run=root,
        coordinator_version=version,
        mode="team",
        parent_catalog=catalog if catalog is not None else [team.delegate_record()],
        resolve_specialist=resolve,
        child_runner=runner,
    )


async def _ok_runner(spec):
    return {"state": "done", "summary": "EVIDENCIA", "evidence": ["ref1"]}


def _call(specialist_uuid, task="investigar X"):
    return {
        "id": "tu1",
        "name": team.delegate_record().anthropic_name,
        "input": {"specialist": specialist_uuid, "task": task},
    }


# --- validação pura: modo, especialista, args ---


def test_so_equipe_delega(user, conversation):
    coord_def, coord_v, _pesq = _setup_team(user)
    out = team.validate_delegation(
        mode="chat",
        delegatable_ids=[str(coord_def.uuid)],
        specialist_uuid=str(coord_def.uuid),
        specialist_complete=True,
        existing_children=0,
        max_child_runs=2,
        parent_depth=0,
    )
    assert out == "not_team"


def test_especialista_fora_dos_delegaveis(user, conversation):
    _, coord_v, pesq = _setup_team(user)
    out = team.validate_delegation(
        mode="team",
        delegatable_ids=[],
        specialist_uuid="00000000-0000-0000-0000-000000000000",
        specialist_complete=True,
        existing_children=0,
        max_child_runs=2,
        parent_depth=0,
    )
    assert out == "unknown_specialist"


def test_especialista_incompleto_bloqueia(user, conversation):
    _, coord_v, pesq = _setup_team(user)
    out = team.validate_delegation(
        mode="team",
        delegatable_ids=[str(pesq.uuid)],
        specialist_uuid=str(pesq.uuid),
        specialist_complete=False,
        existing_children=0,
        max_child_runs=2,
        parent_depth=0,
    )
    assert out == "specialist_incomplete"


def test_args_proibidos(user, conversation):
    assert team.validate_args({"specialist": "s", "task": "t", "model": "m"}) is not None
    assert team.validate_args({"specialist": "s", "task": "t", "code": "x"}) is not None
    assert team.validate_args({"specialist": "s", "task": "  "}) is not None
    assert team.validate_args({"specialist": "s", "task": "ok"}) is None


def test_recursao_bloqueada_profundidade(user, conversation):
    _, coord_v, pesq = _setup_team(user)
    out = team.validate_delegation(
        mode="team",
        delegatable_ids=[str(pesq.uuid)],
        specialist_uuid=str(pesq.uuid),
        specialist_complete=True,
        existing_children=0,
        max_child_runs=2,
        parent_depth=1,
    )
    assert out == "max_depth"


def test_filha_nao_recebe_delegacao_nem_escrita(user, conversation):
    catalog = [*all_records(), team.delegate_record()]
    child = team.child_catalog(catalog)
    ids = {r.stable_id for r in child}
    assert team.DELEGATE_STABLE_ID not in ids
    assert "local:create_study_note" not in ids
    assert "local:calculate" in ids
    assert all(r.approval == "auto" for r in child)


# --- 0 a 2 filhas persistidas ---


async def test_zero_a_duas_filhas_terceira_bloqueia(user, conversation):
    coord_def, coord_v, pesq = await adb(_setup_team)(user)
    root = await adb(_root)(user, conversation, coord_v)
    hook = _hook(user, conversation, root, coord_v, _ok_runner)
    for i in range(2):
        call = _call(str(pesq.uuid), task=f"tarefa {i}")
        call["id"] = f"tu{i}"
        events, block = await hook["handle"](call)
        assert block["is_error"] is False
    call = _call(str(pesq.uuid), task="terceira")
    call["id"] = "tu3"
    events, block = await hook["handle"](call)
    assert block["is_error"] is True
    assert "max_children" in block["content"]
    assert await adb(root.children.count)() == 2
    children = await adb(lambda: list(root.children.order_by("created_at")))()
    assert all(c.depth == 1 for c in children)
    assert all(c.state == "done" for c in children)
    assert children[0].result_summary == "EVIDENCIA"
    assert await adb(Delegation.objects.count)() == 2
    assert await adb(BudgetLedger.objects.filter(kind="child_slot").count)() == 2


async def test_coordenador_sem_slot_bloqueia_na_primeira(user, conversation):
    coord_def, coord_v, pesq = await adb(_setup_team)(user)
    coord_v.max_child_runs = 0
    await adb(coord_v.save)()
    root = await adb(_root)(user, conversation, coord_v)
    hook = _hook(user, conversation, root, coord_v, _ok_runner)
    events, block = await hook["handle"](_call(str(pesq.uuid)))
    assert block["is_error"] is True
    assert "max_children" in block["content"]


async def test_retorno_do_filho_vira_dado_nunca_ordem(user, conversation):
    coord_def, coord_v, pesq = await adb(_setup_team)(user)
    root = await adb(_root)(user, conversation, coord_v)
    hook = _hook(user, conversation, root, coord_v, _ok_runner)
    events, block = await hook["handle"](_call(str(pesq.uuid)))
    assert "EVIDENCIA" in block["content"]
    assert block["type"] == "tool_result"


async def test_falha_do_runner_vira_erro_estruturado(user, conversation):
    async def _boom(spec):
        raise RuntimeError("pane")

    coord_def, coord_v, pesq = await adb(_setup_team)(user)
    root = await adb(_root)(user, conversation, coord_v)
    hook = _hook(user, conversation, root, coord_v, _boom)
    events, block = await hook["handle"](_call(str(pesq.uuid)))
    assert block["is_error"] is True
    assert "child_failed" in block["content"]
    child = await adb(root.children.get)()
    assert child.state == "failed"


# --- paralelo: até 2 simultâneas ---


async def test_duas_filhas_rodam_em_paralelo(user, conversation):
    active, peak = 0, 0

    async def runner(spec):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return {"state": "done", "summary": "ok"}

    out = await team.run_children([{"task": "a"}, {"task": "b"}], runner)
    assert peak == 2
    assert all(err is None for _, err in out)


async def test_semaforo_limita_em_2(user, conversation):
    active, peak = 0, 0

    async def factory(i):
        async def _run():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return i

        return _run

    factories = [await factory(i) for i in range(4)]
    results = await team._gather_capped(factories, 2)
    assert sorted(results) == [0, 1, 2, 3]
    assert peak == 2


async def test_sem_specs_sem_execucao(user, conversation):
    assert await team.run_children([], _ok_runner) == []


# --- permissões e aprovação ---


async def test_escrita_no_filho_exige_aprovacao_fechado(user, conversation):
    from chat.services.tools.context import ExecutionContext

    catalog = all_records()
    write = next(r for r in catalog if r.stable_id == "local:create_study_note")
    ctx = ExecutionContext(user_id=user.pk, conversation_id=conversation.pk)
    out = await _executor.execute_authorized(
        write.anthropic_name,
        {"title": "t", "text": "x"},
        ctx,
        tool_use_id="tu-w",
        approval=None,
        records=catalog,
        run_uuid="child:x",
    )
    assert out["error"]["code"] == "approval_required"
    assert write.stable_id not in {r.stable_id for r in team.child_catalog(catalog)}


def test_isolamento_por_dono(user, conversation, django_user_model):
    other = django_user_model.objects.create_user("boss", password="x")
    _setup_team(other)
    assert team.validate_delegation(
        mode="team",
        delegatable_ids=[],
        specialist_uuid="qualquer",
        specialist_complete=False,
        existing_children=0,
        max_child_runs=2,
        parent_depth=0,
    ) in ("unknown_specialist", "specialist_incomplete")


# --- orçamento global com ledger ---


def test_tetos_puros(user, conversation):
    assert budget.check_depth(0) is None
    assert budget.check_depth(1) == "max_depth"
    assert budget.check_child_count(1) is None
    assert budget.check_child_count(2) == "max_children"
    full = {"generations": 10, "invocations": 0, "output_tokens_used": 0}
    assert budget.budget_exceeded(full) == "budget_generations"
    assert budget.budget_exceeded({"generations": 1, "invocations": 12, "output_tokens_used": 0})
    assert (
        budget.budget_exceeded({"generations": 1, "invocations": 1, "output_tokens_used": 12000})
        == "budget_tokens"
    )
    assert (
        budget.budget_exceeded({"generations": 1, "invocations": 1, "output_tokens_used": 5})
        is None
    )


async def test_ledger_totais_e_bloqueio(user, conversation):
    coord_def, coord_v, pesq = await adb(_setup_team)(user)
    root = await adb(_root)(user, conversation, coord_v)
    await adb(budget.record)(root_run=root, run=root, kind="call_slot", tokens=10)
    await adb(budget.record)(root_run=root, run=root, kind="usage", tokens=50)
    totals = await adb(budget.totals_for)(root)
    assert totals["generations"] == 10
    assert totals["invocations"] == 50
    with pytest.raises(ValueError):
        await adb(budget.record)(root_run=root, run=root, kind="invalido")
    out = team.validate_delegation(
        mode="team",
        delegatable_ids=["s"],
        specialist_uuid="s",
        specialist_complete=True,
        existing_children=0,
        max_child_runs=2,
        parent_depth=0,
        budget_summary=totals,
    )
    assert out == "budget_generations"


# --- cancelamento estruturado ---


async def test_cancel_tree_fecha_raiz_e_filhas(user, conversation):
    coord_def, coord_v, pesq = await adb(_setup_team)(user)
    root = await adb(_root)(user, conversation, coord_v)
    hook = _hook(user, conversation, root, coord_v, _ok_runner)
    await hook["handle"](_call(str(pesq.uuid)))
    await adb(team.spawn_child)(
        owner=user,
        conversation=conversation,
        parent_run=root,
        specialist_version=(await adb(pesq.versions.get)(revision=1)),
        tool_use_id="tu9",
        task="pendente",
    )
    closed = await adb(budget.cancel_tree)(root, reason="cancelled")
    assert closed == 2  # raiz + pendente; a concluída não reabre
    states = await adb(lambda: sorted(c.state for c in root.children.all()))()
    await adb(root.refresh_from_db)()
    assert root.state == "cancelled"
    assert states == ["cancelled", "done"]


async def test_request_cancel_fecha_arvore(user, conversation):
    coord_def, coord_v, pesq = await adb(_setup_team)(user)
    conversation.agent_mode = "team"
    conversation.agent_definition = coord_def
    await adb(conversation.save)()
    conv = await adb(Conversation.objects.select_related("agent_definition").get)(
        pk=conversation.pk
    )
    run, _, _ = await adb(generation.reserve_run)(
        conversation=conv, content="oi", idempotency_key="k-cancel"
    )
    root = await adb(_root)(user, conversation, coord_v)
    run.snapshot = {"team": {"available": True, "parent_run_uuid": str(root.uuid)}}
    await adb(run.save)()
    assert await adb(generation.request_cancel)(str(run.uuid)) is True
    await adb(root.refresh_from_db)()
    assert root.state == "cancelled"


# --- checkpoints e retomada ---


async def test_checkpoint_retomada_pula_concluidas(user, conversation):
    coord_def, coord_v, pesq = await adb(_setup_team)(user)
    root = await adb(_root)(user, conversation, coord_v)
    hook = _hook(user, conversation, root, coord_v, _ok_runner)
    await hook["handle"](_call(str(pesq.uuid)))
    data = await adb(team.checkpoint_data)(root)
    done_child = await adb(root.children.get)()
    specs = [
        {"child_uuid": str(done_child.uuid), "task": "repetir?"},
        {"child_uuid": "nova", "task": "nova"},
    ]
    pending = team.pending_specs(specs, data["completed_child_uuids"])
    assert [s["child_uuid"] for s in pending] == ["nova"]


def test_validate_child_result_rejeita(user, conversation):
    assert team.validate_child_result("texto")[1] == "invalid_child_result"
    assert team.validate_child_result({"state": "x", "summary": "s"})[1] == "invalid_child_state"
    assert team.validate_child_result({"state": "done", "summary": "  "})[1] == (
        "empty_child_summary"
    )
    ok, err = team.validate_child_result({"state": "done", "summary": " s "})
    assert err is None and ok["summary"] == "s"


# --- ponta a ponta com SDK simulado ---


async def test_equipe_delega_e_sintetiza(user, conversation):
    coord_def, coord_v, pesq = await adb(_setup_team)(user)

    def _setup():
        conversation.agent_mode = "team"
        conversation.agent_definition = coord_def
        conversation.save()

    await adb(_setup)()
    conv = await adb(Conversation.objects.select_related("agent_definition").get)(
        pk=conversation.pk
    )
    run, _, _ = await adb(generation.reserve_run)(
        conversation=conv, content="pesquise X", idempotency_key="k-team"
    )
    delegate_name = team.delegate_record().anthropic_name
    parent_tool = FakeToolMessage(
        [FakeToolUseBlock("tu1", delegate_name, {"specialist": str(pesq.uuid), "task": "buscar X"})]
    )
    child_text = FakeFinalMessage("EVIDENCIA-DO-FILHO", output_tokens=7)
    parent_final = FakeFinalMessage("RESPOSTA-FINAL")
    ns = FakeMessagesNamespace(
        create_results=[parent_tool, child_text, parent_final], count_value=50
    )
    client = FakeClient(ns)
    events = [e async for e in generation.execute_run(str(run.uuid), user=user, client=client)]
    kinds = [e["type"] for e in events]
    assert "run_started" in kinds
    assert kinds[-1] == "done"
    assert events[-1]["text"] == "RESPOSTA-FINAL"
    root = await adb(AgentRun.objects.get)(
        conversation=conversation, parent__isnull=True, agent_version=coord_v
    )
    assert await adb(root.children.count)() == 1
    child = await adb(root.children.get)()
    assert child.depth == 1 and child.state == "done"
    assert "EVIDENCIA-DO-FILHO" in child.result_summary
    assert await adb(Delegation.objects.filter(child_run=child).count)() == 1
    assert await adb(BudgetLedger.objects.filter(kind="child_slot").count)() == 1
    await adb(run.refresh_from_db)()
    assert run.snapshot["team"]["available"] is True
    tools_sent = ns.calls["create"][0].get("tools") or []
    assert delegate_name in [t["name"] for t in tools_sent]
    child_kwargs = ns.calls["create"][1]
    child_tools = child_kwargs.get("tools") or []
    assert delegate_name not in [t["name"] for t in child_tools]


async def test_chat_nao_expoe_delegacao(user, conversation):
    await adb(_setup_team)(user)
    run, _, _ = await adb(generation.reserve_run)(
        conversation=conversation, content="oi", idempotency_key="k-noteam"
    )
    from tests.fakes import FakeStream, FakeStreamManager

    final = FakeFinalMessage("ok")
    client = FakeClient(
        FakeMessagesNamespace(stream_manager=FakeStreamManager(FakeStream(["ok"], final)))
    )
    events = [e async for e in generation.execute_run(str(run.uuid), user=user, client=client)]
    assert events[-1]["type"] == "done"
    await adb(run.refresh_from_db)()
    assert "team" not in run.snapshot
