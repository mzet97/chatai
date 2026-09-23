"""M2: CachePlanner + métricas (AG-5).

Sem chamadas pagas: SDK sempre simulado (tests/fakes.py). Sem commit.
Cobre: modos/TTL, precedência perfil+override, breakpoints determinísticos,
sem padding, sem marcar thinking, sem acúmulo de marcadores, payload
idêntico com/sem cache, métricas agregadas sem dupla contagem.
"""

import json
from types import SimpleNamespace

import pytest
from asgiref.sync import sync_to_async

from chat.models import Conversation
from chat.models_agents import AgentDefinition
from chat.models_tools import ModelStep
from chat.services import generation
from chat.services.agents import cache, effective, profiles
from tests.fakes import (
    FakeClient,
    FakeFinalMessage,
    FakeMessagesNamespace,
    FakeStream,
    FakeStreamManager,
)

pytestmark = pytest.mark.django_db(transaction=True)
adb = sync_to_async

SYSTEM = [{"type": "text", "text": "POLITICA"}, {"type": "text", "text": "INSTRUCOES"}]
MESSAGES = [{"role": "user", "content": "oi"}]


def _payload(**extra):
    base = {"model": "m", "system": [dict(b) for b in SYSTEM], "messages": [dict(MESSAGES[0])]}
    base.update(extra)
    return base


# --- normalização (desconhecido = desabilitado) ---


def test_modo_desconhecido_vira_disabled_e_ttl_desconhecido_vira_5m():
    assert cache.normalize_mode("turbo") == "disabled"
    assert cache.normalize_mode("") == "disabled"
    assert cache.normalize_mode(None) == "disabled"
    assert cache.normalize_ttl("2h") == "5m"
    assert cache.normalize_ttl("") == "5m"
    assert cache.normalize_ttl(None) == "5m"
    assert cache.normalize_mode("stable") == "stable"
    assert cache.normalize_ttl("1h") == "1h"


# --- precedência override → perfil → padrões ---


def test_resolve_override_vence_perfil():
    out = cache.resolve("stable", "5m", override={"mode": "conversation", "ttl": "1h"})
    assert (out["mode"], out["ttl"]) == ("conversation", "1h")
    assert out["origins"] == {"mode": "override", "ttl": "override"}


def test_resolve_perfil_quando_sem_override_e_padrao_quando_vazio():
    out = cache.resolve("stable", "1h", override={})
    assert (out["mode"], out["ttl"], out["origins"]["mode"]) == ("stable", "1h", "agent_version")
    out = cache.resolve("", "", override={})
    assert (out["mode"], out["ttl"]) == ("stable", "5m")
    assert out["origins"] == {"mode": "default", "ttl": "default"}


def test_resolve_override_invalido_cai_para_perfil():
    out = cache.resolve("stable", "5m", override={"mode": "turbo", "ttl": "2h"})
    assert (out["mode"], out["ttl"]) == ("stable", "5m")
    assert out["origins"] == {"mode": "agent_version", "ttl": "agent_version"}
    assert len(out["notes"]) == 2


# --- elegibilidade determinística (sem padding) ---


def test_plan_disabled_e_vazio_sao_inelegiveis_sem_marcador():
    p = cache.plan(mode="disabled", ttl="5m", system=SYSTEM, messages=MESSAGES)
    assert (p["eligible"], p["strategy"], p["diagnosis"]) == (False, "none", "disabled")
    out = cache.apply_to_payload(_payload(), p)
    assert out == _payload() and "cache_control" not in json.dumps(out)
    p = cache.plan(mode="stable", ttl="5m", system=[], messages=[])
    assert p["eligible"] is False and p["diagnosis"] == "empty_prefix"
    assert cache.apply_to_payload(_payload(system=[], messages=[]), p) == {
        "model": "m",
        "system": [],
        "messages": [],
    }


def test_plan_stable_exige_system_e_tools_deterministicas():
    p = cache.plan(mode="stable", ttl="5m", system=[], messages=MESSAGES)
    assert (p["eligible"], p["diagnosis"]) == (False, "no_system_prefix")
    p = cache.plan(
        mode="stable", ttl="5m", system=SYSTEM, messages=MESSAGES, tools_deterministic=False
    )
    assert (p["eligible"], p["diagnosis"]) == (False, "nondeterministic_tools")
    p = cache.plan(mode="stable", ttl="5m", system=SYSTEM, messages=MESSAGES)
    assert (p["eligible"], p["strategy"]) == (True, "system_breakpoint")


def test_plan_registra_thinking_sem_marcar():
    p = cache.plan(mode="stable", ttl="5m", system=SYSTEM, messages=MESSAGES, thinking_present=True)
    assert p["eligible"] is True and "thinking_unmarked" in p["diagnosis"]
    out = cache.apply_to_payload(_payload(thinking={"type": "enabled"}), p)
    assert out["thinking"] == {"type": "enabled"}  # intocado
    assert "cache_control" not in json.dumps(out["thinking"])


# --- aplicação: breakpoints determinísticos, sem acúmulo ---


def test_stable_marca_so_ultimo_bloco_system_com_um_ttl():
    p = cache.plan(mode="stable", ttl="1h", system=SYSTEM, messages=MESSAGES)
    out = cache.apply_to_payload(_payload(), p)
    assert out["system"][0] == {"type": "text", "text": "POLITICA"}
    assert out["system"][1] == {
        "type": "text",
        "text": "INSTRUCOES",
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
    }
    assert "cache_control" not in out  # sem top-level junto (nunca dois)
    assert out["messages"] == MESSAGES


def test_aplicacao_idempotente_sem_marcadores_acumulados():
    p = cache.plan(mode="stable", ttl="5m", system=SYSTEM, messages=MESSAGES)
    once = cache.apply_to_payload(_payload(), p)
    twice = cache.apply_to_payload(once, p)
    assert twice == once
    assert json.dumps(twice).count("cache_control") == 1


def test_conversation_marca_top_level_e_nao_toca_nos_blocos():
    p = cache.plan(mode="conversation", ttl="5m", system=SYSTEM, messages=MESSAGES)
    out = cache.apply_to_payload(_payload(), p)
    assert out["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
    assert out["system"] == SYSTEM and out["messages"] == MESSAGES


def test_payload_identico_com_e_sem_cache_exceto_marcadores():
    disabled = cache.plan(mode="disabled", ttl="5m", system=SYSTEM, messages=MESSAGES)
    stable = cache.plan(mode="stable", ttl="5m", system=SYSTEM, messages=MESSAGES)
    a = cache.apply_to_payload(_payload(), disabled)
    b = cache.apply_to_payload(_payload(), stable)
    assert a == _payload()  # sem cache: byte-idêntico
    assert cache.payload_identity_ignored(a, b)  # diferem só no marcador
    assert b["system"][0] == SYSTEM[0] and b["messages"] == MESSAGES


def test_apply_nao_muta_entrada():
    p = cache.plan(mode="stable", ttl="5m", system=SYSTEM, messages=MESSAGES)
    src = _payload()
    cache.apply_to_payload(src, p)
    assert src == _payload()


# --- métricas: só confirmado, ausente = desconhecido ---


def test_parse_usage_ausente_e_none():
    assert cache.parse_usage(None) == {
        "input_tokens": None,
        "cache_creation_input_tokens": None,
        "cache_read_input_tokens": None,
        "cache_creation_detail": None,
    }
    out = cache.parse_usage(SimpleNamespace(input_tokens=10, output_tokens=5))
    assert out["cache_creation_input_tokens"] is None  # nunca zero presumido
    assert out["cache_read_input_tokens"] is None
    assert out["cache_creation_detail"] is None


def test_parse_usage_com_breakdown_por_ttl():
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=5,
        cache_creation_input_tokens=80,
        cache_read_input_tokens=10,
        cache_creation=SimpleNamespace(ephemeral_5m_input_tokens=80, ephemeral_1h_input_tokens=0),
    )
    out = cache.parse_usage(usage)
    assert out["cache_creation_input_tokens"] == 80
    assert out["cache_read_input_tokens"] == 10
    assert out["cache_creation_detail"] == {
        "ephemeral_5m_input_tokens": 80,
        "ephemeral_1h_input_tokens": 0,
    }


def test_aggregate_soma_sem_dupla_contagem_e_fracao_zero_protegida():
    recs = [
        {
            "key": "s0",
            "input_tokens": 100,
            "cache_creation_input_tokens": 80,
            "cache_read_input_tokens": 0,
        },
        {
            "key": "s1",
            "input_tokens": 60,
            "cache_creation_input_tokens": None,
            "cache_read_input_tokens": 40,
        },
        {
            "key": "s1",
            "input_tokens": 60,
            "cache_creation_input_tokens": None,
            "cache_read_input_tokens": 40,
        },  # replay da mesma etapa: conta 1x
        {
            "key": "s2",
            "input_tokens": None,
            "cache_creation_input_tokens": None,
            "cache_read_input_tokens": None,
        },  # desconhecido: ignorado, não zera
    ]
    out = cache.aggregate_usage(recs)
    assert out["calls"] == 3
    assert out["input_tokens"] == 160
    assert out["cache_creation_input_tokens"] == 80
    assert out["cache_read_input_tokens"] == 40
    assert out["uncached_input_tokens"] == 40  # 160 - 80 - 40
    assert out["cache_read_fraction"] == pytest.approx(40 / 160)
    assert out["calls_with_confirmed_cache"] == 2


def test_aggregate_denominador_zero_e_vazio():
    assert cache.aggregate_usage([])["cache_read_fraction"] is None
    out = cache.aggregate_usage([{"input_tokens": 0}])
    assert out["cache_read_fraction"] is None
    assert out["input_tokens"] == 0


# --- controles por perfil + override (conversa) ---


def _publish_geral(owner, model="m-m2", instructions="INSTR"):
    profiles.ensure_examples(owner)
    geral = AgentDefinition.objects.get(owner=owner, name="Geral")
    v1 = geral.versions.get(revision=1)
    v1.model = model
    v1.task_instructions = instructions
    v1.cache_mode = "stable"
    v1.cache_ttl = "1h"
    v1.save()
    profiles.publish_version(v1.pk, owner)
    v1.refresh_from_db()
    return geral, v1


def test_efetivo_usa_perfil_quando_conversa_sem_override(user, conversation):
    geral, _ = _publish_geral(user)
    conversation.agent_mode = "agent"
    conversation.agent_definition = geral
    conversation.save()
    out = effective.resolve_agent(conversation)
    assert out["values"]["cache_mode"] == "stable"
    assert out["values"]["cache_ttl"] == "1h"
    assert out["origins"]["cache_mode"] == "agent_version"


def test_efetivo_override_da_conversa_vence_perfil(user, conversation):
    geral, _ = _publish_geral(user)
    conversation.agent_mode = "agent"
    conversation.agent_definition = geral
    conversation.cache_mode = "conversation"
    conversation.cache_ttl = "5m"
    conversation.save()
    out = effective.resolve_agent(conversation)
    assert out["values"]["cache_mode"] == "conversation"
    assert out["values"]["cache_ttl"] == "5m"
    assert out["origins"]["cache_mode"] == "conversation"
    assert out["origins"]["cache_ttl"] == "conversation"


def test_efetivo_override_invalido_cai_para_perfil(user, conversation):
    geral, _ = _publish_geral(user)
    conversation.agent_mode = "agent"
    conversation.agent_definition = geral
    conversation.cache_mode = "turbo"  # legado/inválido: perfil prevalece
    conversation.save()
    out = effective.resolve_agent(conversation)
    assert out["values"]["cache_mode"] == "stable"
    assert out["origins"]["cache_mode"] == "agent_version"


def test_patch_cache_override_valido_e_invalido(logged_client, conversation):
    conv = conversation
    r = logged_client.patch(
        f"/api/conversations/{conv.uuid}",
        data=json.dumps({"cache_mode": "conversation", "cache_ttl": "1h"}),
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["cache_mode"] == "conversation"
    assert r.json()["cache_ttl"] == "1h"
    r = logged_client.patch(
        f"/api/conversations/{conv.uuid}",
        data=json.dumps({"cache_mode": "turbo"}),
        content_type="application/json",
    )
    assert r.status_code == 400
    conv.refresh_from_db()
    assert conv.cache_mode == "conversation"  # inválido não salvou
    r = logged_client.patch(
        f"/api/conversations/{conv.uuid}",
        data=json.dumps({"cache_mode": "", "cache_ttl": ""}),
        content_type="application/json",
    )
    assert r.status_code == 200
    conv.refresh_from_db()
    assert conv.cache_mode == "" and conv.cache_ttl == ""


# --- geração com fakes (sem chamadas pagas) ---


def _client_ok(final):
    mgr = FakeStreamManager(FakeStream(["ok"], final))
    return FakeClient(FakeMessagesNamespace(stream_manager=mgr))


async def _drain(run_id, user, client):
    return [e async for e in generation.execute_run(run_id, user=user, client=client)]


async def test_geracao_agente_stable_aplica_breakpoint_e_snapshot(user, conversation):
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
        conversation=conv, content="oi", idempotency_key="k-m2-stable"
    )
    client = _client_ok(FakeFinalMessage("ok"))
    events = await _drain(str(run.uuid), user, client)
    assert events[0]["type"] == "run_started"
    (stream_kwargs,) = client.messages.calls["stream"]
    system = stream_kwargs["system"]
    assert system[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert all("cache_control" not in b for b in system[:-1])
    assert "cache_control" not in stream_kwargs  # stable: sem top-level
    assert "thinking" not in stream_kwargs  # pensamento padrão não marcado
    await adb(run.refresh_from_db)()
    snap_cache = run.snapshot["cache"]
    assert snap_cache["applied"] is True
    assert snap_cache["strategy"] == "system_breakpoint"
    assert snap_cache["origins"] == {"mode": "agent_version", "ttl": "agent_version"}
    assert snap_cache["usage"]["cache_creation_input_tokens"] is None  # fake sem cache


async def test_geracao_payload_identico_com_e_sem_cache(user, conversation):
    async def _run_once(cache_mode, key):
        def _setup():
            geral, _ = _publish_geral(user)
            # Conversa isolada por execução: o histórico não pode diferir.
            conv = Conversation.objects.create(owner=user, title="t-m2")
            conv.agent_mode = "agent"
            conv.agent_definition = geral
            conv.cache_mode = cache_mode
            conv.save()
            return conv.pk

        conv_pk = await adb(_setup)()
        conv = await adb(Conversation.objects.select_related("agent_definition").get)(pk=conv_pk)
        run, _, _ = await adb(generation.reserve_run)(
            conversation=conv, content="oi", idempotency_key=key
        )
        client = _client_ok(FakeFinalMessage("ok"))
        await _drain(str(run.uuid), user, client)
        await adb(run.refresh_from_db)()
        return client.messages.calls["stream"][0], run.snapshot["cache"]

    kwargs_on, snap_on = await _run_once("", "k-m2-id-on")  # perfil stable/1h
    kwargs_off, snap_off = await _run_once("disabled", "k-m2-id-off")
    assert snap_on["applied"] is True and snap_off["applied"] is False
    assert cache.payload_identity_ignored(kwargs_off, kwargs_on)
    texts_on = [b.get("text", "") for b in kwargs_on["system"]]
    texts_off = [b.get("text", "") for b in kwargs_off["system"]]
    assert texts_on == texts_off  # conteúdo idêntico, só o marcador difere
    assert kwargs_on["messages"] == kwargs_off["messages"]
    assert "cache_control" not in json.dumps(kwargs_off)


async def test_geracao_chat_sem_marcadores(user, conversation):
    await adb(_publish_geral)(user)  # perfis existem; conversa segue em chat
    run, _, _ = await adb(generation.reserve_run)(
        conversation=conversation, content="oi", idempotency_key="k-m2-chat"
    )
    client = _client_ok(FakeFinalMessage("ok"))
    await _drain(str(run.uuid), user, client)
    (stream_kwargs,) = client.messages.calls["stream"]
    assert "cache_control" not in json.dumps(stream_kwargs)
    await adb(run.refresh_from_db)()
    assert run.snapshot["cache"]["applied"] is False


async def test_geracao_registra_uso_de_cache_confirmado(user, conversation):
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
        conversation=conv, content="oi", idempotency_key="k-m2-usage"
    )
    final = FakeFinalMessage("ok")
    final.usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=5,
        cache_creation_input_tokens=80,
        cache_read_input_tokens=10,
        cache_creation=SimpleNamespace(ephemeral_5m_input_tokens=20, ephemeral_1h_input_tokens=60),
    )
    events = await _drain(str(run.uuid), user, _client_ok(final))
    usage_ev = next(e for e in events if e["type"] == "usage")
    assert usage_ev["cache_creation_input_tokens"] == 80
    assert usage_ev["cache_read_input_tokens"] == 10
    await adb(run.refresh_from_db)()
    snap_usage = run.snapshot["cache"]["usage"]
    assert snap_usage["cache_creation_input_tokens"] == 80
    assert snap_usage["cache_read_input_tokens"] == 10
    assert snap_usage["cache_creation_detail"] == {
        "ephemeral_5m_input_tokens": 20,
        "ephemeral_1h_input_tokens": 60,
    }


def test_save_step_persiste_cache_sem_dupla_contagem():
    from chat.services.tools import chat_loop as _loop

    message = SimpleNamespace(
        model="m",
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=50,
            output_tokens=3,
            cache_creation_input_tokens=30,
            cache_read_input_tokens=5,
            cache_creation=SimpleNamespace(
                ephemeral_5m_input_tokens=30, ephemeral_1h_input_tokens=0
            ),
        ),
    )
    _loop._save_step("run-m2", 0, message, [])
    step = ModelStep.objects.get(run_uuid="run-m2", step=0)
    assert step.cache_creation_input_tokens == 30
    assert step.cache_read_input_tokens == 5
    assert step.cache_creation_detail == {
        "ephemeral_5m_input_tokens": 30,
        "ephemeral_1h_input_tokens": 0,
    }
    recs = [
        {
            "key": f"{s.run_uuid}:{s.step}",
            "input_tokens": s.input_tokens,
            "cache_creation_input_tokens": s.cache_creation_input_tokens,
            "cache_read_input_tokens": s.cache_read_input_tokens,
        }
        for s in ModelStep.objects.filter(run_uuid="run-m2")
    ]
    out = cache.aggregate_usage(recs)
    assert (out["input_tokens"], out["cache_creation_input_tokens"]) == (50, 30)
