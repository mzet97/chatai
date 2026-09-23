"""M3: pensamento por agente (AG-4).

Sem chamadas pagas: SDK sempre simulado (tests/fakes.py). Sem commit.
Cobre: validação do PATCH de versão, precedência conversa→versão, valores
efetivos visíveis no detalhe, adaptativo/legado/obrigatório/desconhecido,
conflito com temperatura, replay separado pai/filhos e painel preservado.
"""

import json

import pytest
from asgiref.sync import sync_to_async

from chat.models import Conversation
from chat.models_agents import AgentDefinition
from chat.services import generation
from chat.services import thinking as _thinking
from chat.services.agents import effective, profiles
from chat.services.agents import thinking as _agent_thinking
from chat.services.thinking_stream import replay as _replay
from chat.services.thinking_stream import store as _store
from tests.fakes import (
    FakeClient,
    FakeFinalMessage,
    FakeMessagesNamespace,
    FakeStream,
    FakeStreamManager,
)

pytestmark = pytest.mark.django_db(transaction=True)
adb = sync_to_async

HAIKU = "claude-haiku-4-5-20251001"  # com temperatura suportada, sem pensamento tabelado


def _publish(owner, model=HAIKU, **think):
    profiles.ensure_examples(owner)
    geral = AgentDefinition.objects.get(owner=owner, name="Geral")
    v1 = geral.versions.get(revision=1)
    v1.model = model
    for k, v in think.items():
        setattr(v1, k, v)
    v1.save()
    profiles.publish_version(v1.pk, owner)
    v1.refresh_from_db()
    return geral, v1


def _version_uuid(definition):
    return str(definition.versions.get(revision=1).uuid)


def _patch_version(client, vuuid, body):
    return client.patch(
        f"/api/agent-versions/{vuuid}", data=json.dumps(body), content_type="application/json"
    )


def _client_ok(text="ok"):
    final = FakeFinalMessage(text)
    mgr = FakeStreamManager(FakeStream([text], final))
    return FakeClient(FakeMessagesNamespace(stream_manager=mgr))


async def _drain(run_id, user, client):
    return [e async for e in generation.execute_run(run_id, user=user, client=client)]


async def _run_agent(user, conv_pk, key):
    conv = await adb(Conversation.objects.select_related("agent_definition").get)(pk=conv_pk)
    run, _, _ = await adb(generation.reserve_run)(
        conversation=conv, content="oi", idempotency_key=key
    )
    client = _client_ok()
    events = await _drain(str(run.uuid), user, client)
    await adb(run.refresh_from_db)()
    return events, client, run


def _setup_agent_conversation(user, conversation, **think):
    geral, _ = _publish(user, **think)
    conversation.agent_mode = "agent"
    conversation.agent_definition = geral
    conversation.save()
    return conversation.pk


# --- validação do PATCH de versão (M3/AG-4.1) ---


def test_versao_rejeita_pensamento_invalido_sem_salvar(logged_client, user):
    profiles.ensure_examples(user)
    geral = AgentDefinition.objects.get(owner=user, name="Geral")
    vuuid = _version_uuid(geral)
    for body in (
        {"thinking_mode": "turbo"},
        {"thinking_level": "extremo"},
        {"thinking_budget": 999},
        {"thinking_budget": "2048"},
        {"thinking_budget": None},
        {"variation_level": "turbo"},
        {"cache_mode": "turbo"},
        {"cache_ttl": "2h"},
    ):
        r = _patch_version(logged_client, vuuid, body)
        assert r.status_code == 400, body
        assert r.json()["code"] == "validation"
    v1 = geral.versions.get(revision=1)
    v1.refresh_from_db()
    assert (v1.thinking_mode, v1.thinking_level, v1.thinking_budget) == (
        "default",
        "medium",
        1024,
    )


def test_versao_aceita_pensamento_valido(logged_client, user):
    profiles.ensure_examples(user)
    geral = AgentDefinition.objects.get(owner=user, name="Geral")
    r = _patch_version(
        logged_client,
        _version_uuid(geral),
        {"thinking_mode": "enabled", "thinking_level": "high", "thinking_budget": 2048},
    )
    assert r.status_code == 200
    assert r.json()["thinking_mode"] == "enabled"
    assert r.json()["thinking_budget"] == 2048


def test_versao_publicada_continua_imutavel(logged_client, user):
    geral, _ = _publish(user)
    r = _patch_version(logged_client, _version_uuid(geral), {"thinking_mode": "enabled"})
    assert r.status_code == 409


# --- precedência + valores efetivos visíveis (AG-1.3) ---


def test_override_da_conversa_vence_versao_no_pensamento(user, conversation):
    _publish(user, thinking_mode="disabled", thinking_level="low", thinking_budget=4096)
    geral = AgentDefinition.objects.get(owner=user, name="Geral")
    conversation.agent_mode = "agent"
    conversation.agent_definition = geral
    conversation.thinking_mode = "enabled"
    conversation.thinking_level = "high"
    conversation.save()
    out = effective.resolve_agent(conversation)
    assert out["values"]["thinking_mode"] == "enabled"
    assert out["origins"]["thinking_mode"] == "conversation"
    assert out["values"]["thinking_level"] == "high"
    assert out["values"]["thinking_budget"] == 4096
    assert out["origins"]["thinking_budget"] == "agent_version"


def test_detalhe_expoe_valores_efetivos_do_agente(logged_client, user, conversation):
    _publish(user, thinking_mode="enabled", thinking_level="high", thinking_budget=2048)
    geral = AgentDefinition.objects.get(owner=user, name="Geral")
    conversation.agent_mode = "agent"
    conversation.agent_definition = geral
    conversation.save()
    d = logged_client.get(f"/api/conversations/{conversation.uuid}").json()
    eff = d["agent_effective"]
    assert eff["applied"] is True and eff["definition"] == "Geral"
    assert eff["version_revision"] == 1
    assert eff["values"]["thinking_mode"] == "enabled"
    assert eff["origins"]["thinking_mode"] == "agent_version"
    assert eff["values"]["model"] == HAIKU
    # Override da conversa aparece com origem visível.
    d2 = logged_client.patch(
        f"/api/conversations/{conversation.uuid}",
        data=json.dumps({"thinking_mode": "disabled"}),
        content_type="application/json",
    ).json()
    assert d2["agent_effective"]["values"]["thinking_mode"] == "disabled"
    assert d2["agent_effective"]["origins"]["thinking_mode"] == "conversation"


def test_detalhe_chat_sem_agente_marca_nao_aplicado(logged_client, conversation):
    d = logged_client.get(f"/api/conversations/{conversation.uuid}").json()
    assert d["agent_effective"] == {"applied": False, "mode": "chat"}


# --- resolução por capacidade (unidade, sem pagas) ---

CAPS = {"adaptive": {"m-adapt"}, "legacy": {"m-legacy"}, "always_on": {"m-always"}}


def _values(**kw):
    base = {"thinking_mode": "enabled", "thinking_level": "high", "thinking_budget": 2048}
    base.update(kw)
    return base


def _origins(**kw):
    base = {"thinking_mode": "agent_version", "thinking_level": "agent_version"}
    base.update(kw)
    return base


def test_adaptativo_mapeia_effort_e_display_com_origens():
    out = _agent_thinking.resolve_for_agent(
        values=_values(),
        origins=_origins(),
        model="m-adapt",
        base_url="https://api.anthropic.com",
        max_tokens=4096,
        show_summary=True,
        **CAPS,
    )
    assert out["think"]["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert out["think"]["output_config"] == {"effort": "high"}
    assert out["active"] is True
    assert out["effective"]["display"] == "summarized"
    assert out["origins"] == {
        "mode": "agent_version",
        "level": "agent_version",
        "budget": "default",
        "show_summary": "conversation",
    }


def test_legado_envia_orcamento_e_conflito_vem_tipado():
    out = _agent_thinking.resolve_for_agent(
        values=_values(thinking_level="medium", thinking_budget=1024),
        origins=_origins(),
        model="m-legacy",
        base_url="https://api.anthropic.com",
        max_tokens=4096,
        **CAPS,
    )
    assert out["think"]["thinking"] == {
        "type": "enabled",
        "budget_tokens": 1024,
        "display": "omitted",
    }
    assert out["think"]["conflict"] is None
    curto = _agent_thinking.resolve_for_agent(
        values=_values(thinking_budget=4096),
        origins=_origins(),
        model="m-legacy",
        base_url="https://api.anthropic.com",
        max_tokens=1024,
        **CAPS,
    )
    assert curto["think"]["thinking"] is None
    assert curto["think"]["conflict"] == {"budget": 4096, "ceiling": 1024}
    assert curto["active"] is False


def test_obrigatorio_nunca_envia_chaves_e_bloqueia_desligar():
    ligado = _agent_thinking.resolve_for_agent(
        values=_values(),
        origins=_origins(),
        model="m-always",
        base_url="https://api.anthropic.com",
        max_tokens=4096,
        show_summary=True,
        **CAPS,
    )
    assert ligado["think"]["thinking"] is None
    assert ligado["think"]["output_config"] is None
    assert ligado["effective"]["display"] == "summarized"
    assert ligado["active"] is False
    desligar = _agent_thinking.resolve_for_agent(
        values=_values(thinking_mode="disabled"),
        origins=_origins(),
        model="m-always",
        base_url="https://api.anthropic.com",
        max_tokens=4096,
        **CAPS,
    )
    assert desligar["think"]["state"] == "always_on" and desligar["think"]["reason"]
    assert desligar["active"] is False


def test_desconhecido_omite_tudo_e_preserva_preferencia():
    out = _agent_thinking.resolve_for_agent(
        values=_values(),
        origins=_origins(),
        model="m-???",
        base_url="https://api.anthropic.com",
        max_tokens=4096,
    )
    assert out["think"]["thinking"] is None and out["think"]["output_config"] is None
    assert out["think"]["state"] == "unknown" and out["think"]["reason"]
    assert out["active"] is False
    # Preferência do perfil preservada no bloco efetivo, mesmo sem enviar.
    assert out["effective"]["mode"] == "enabled"
    assert out["effective"]["level"] == "high"


def test_validate_fields_so_barra_presente_invalido():
    assert _agent_thinking.validate_fields({}) == {}
    assert _agent_thinking.validate_fields({"model": "x"}) == {}
    assert _agent_thinking.validate_fields({"thinking_mode": "enabled"}) == {}
    errors = _agent_thinking.validate_fields({"thinking_mode": "turbo", "thinking_budget": 7})
    assert set(errors) == {"thinking_mode", "thinking_budget"}


# --- geração com fakes (sem chamadas pagas) ---


async def test_geracao_agente_adaptativo_envia_thinking_e_omite_temperatura(
    user, conversation, monkeypatch
):
    monkeypatch.setattr(_thinking, "CAP_ADAPTIVE_MODELS", frozenset({HAIKU}))
    conv_pk = await adb(_setup_agent_conversation)(
        user, conversation, thinking_mode="enabled", thinking_level="high"
    )
    events, client, run = await _run_agent(user, conv_pk, "k-m3-adapt")
    assert events[0]["type"] == "run_started"
    (stream_kwargs,) = client.messages.calls["stream"]
    assert stream_kwargs["thinking"] == {"type": "adaptive", "display": "omitted"}
    assert stream_kwargs["output_config"] == {"effort": "high"}
    assert "temperature" not in json.dumps(stream_kwargs)
    snap = run.snapshot["agent_thinking"]
    assert snap["applied"] is True
    assert snap["effective"]["mode"] == "enabled"
    assert snap["effective"]["display"] == "omitted"
    assert snap["origins"]["mode"] == "agent_version"
    assert snap["sent"] is True and snap["effort_sent"] == "high"
    assert snap["temperature_omitted"] is True
    assert run.snapshot["temperature_not_applied"] == "thinking"


async def test_geracao_agente_legado_com_orcamento(user, conversation, monkeypatch):
    monkeypatch.setattr(_thinking, "CAP_LEGACY_MODELS", frozenset({HAIKU}))

    def _setup():
        conv_pk = _setup_agent_conversation(
            user, conversation, thinking_mode="enabled", thinking_budget=1024
        )
        conv = Conversation.objects.get(pk=conv_pk)
        conv.max_output_tokens = 4096  # teto comporta o orçamento
        conv.save()
        return conv_pk

    conv_pk = await adb(_setup)()
    events, client, run = await _run_agent(user, conv_pk, "k-m3-legacy")
    assert events[0]["type"] == "run_started"
    (stream_kwargs,) = client.messages.calls["stream"]
    assert stream_kwargs["thinking"]["type"] == "enabled"
    assert stream_kwargs["thinking"]["budget_tokens"] == 1024
    assert "temperature" not in json.dumps(stream_kwargs)
    assert run.snapshot["agent_thinking"]["sent"] is True


async def test_geracao_agente_legado_conflito_bloqueia_sem_chamada(user, conversation, monkeypatch):
    monkeypatch.setattr(_thinking, "CAP_LEGACY_MODELS", frozenset({HAIKU}))
    # Teto padrão (1024) não comporta nenhum orçamento → thinking_conflict.
    conv_pk = await adb(_setup_agent_conversation)(
        user, conversation, thinking_mode="enabled", thinking_budget=1024
    )
    events, client, run = await _run_agent(user, conv_pk, "k-m3-conflict")
    assert len(events) == 1 and events[0]["type"] == "error"
    assert events[0]["code"] == "thinking_conflict"
    assert client.messages.calls["stream"] == []  # bloqueou antes da chamada paga
    assert run.snapshot["thinking_state"] == "legacy"
    assert run.snapshot["thinking_reason"]


async def test_geracao_agente_desconhecido_omite_mas_temperatura_vai(user, conversation):
    conv_pk = await adb(_setup_agent_conversation)(
        user, conversation, thinking_mode="enabled", thinking_level="high"
    )
    events, client, run = await _run_agent(user, conv_pk, "k-m3-unknown")
    assert events[0]["type"] == "run_started"
    (stream_kwargs,) = client.messages.calls["stream"]
    assert "thinking" not in stream_kwargs and "output_config" not in stream_kwargs
    assert stream_kwargs["extra_body"] == {"temperature": 0.5}
    assert run.snapshot["requested_thinking_mode"] == "enabled"  # preferência preservada
    assert run.snapshot["thinking_sent"] is False
    assert run.snapshot["thinking_reason"]
    assert run.snapshot["agent_thinking"]["applied"] is True
    assert run.snapshot["agent_thinking"]["sent"] is False
    assert run.snapshot["agent_thinking"]["temperature_omitted"] is False


async def test_geracao_agente_obrigatorio_bloqueia_desligar_mas_gera(
    user, conversation, monkeypatch
):
    monkeypatch.setattr(_thinking, "CAP_ALWAYS_ON_MODELS", frozenset({HAIKU}))
    conv_pk = await adb(_setup_agent_conversation)(user, conversation, thinking_mode="disabled")
    events, client, run = await _run_agent(user, conv_pk, "k-m3-always")
    assert events[0]["type"] == "run_started"
    (stream_kwargs,) = client.messages.calls["stream"]
    assert "thinking" not in stream_kwargs  # nunca envia chave inválida
    assert run.snapshot["thinking_state"] == "always_on"
    assert run.snapshot["thinking_reason"]
    assert run.snapshot["agent_thinking"]["sent"] is False


async def test_chat_nao_tem_bloco_agent_thinking(user, conversation):
    await adb(_publish)(user)
    run, _, _ = await adb(generation.reserve_run)(
        conversation=conversation, content="oi", idempotency_key="k-m3-chat"
    )
    client = _client_ok()
    events = await _drain(str(run.uuid), user, client)
    assert events[0]["type"] == "run_started"
    (stream_kwargs,) = client.messages.calls["stream"]
    assert "thinking" not in stream_kwargs
    await adb(run.refresh_from_db)()
    assert "agent_thinking" not in run.snapshot


# --- replay separado pai/filhos (AG-4.2/T-G6) + painel preservado ---


def _log(text="penso logo existo", sig="sig-1", display="summarized"):
    log = _store.new_log(display=display)
    _store.append_delta(log, thinking=text, signature=sig)
    return _store.complete(log)


def test_replay_somente_do_mesmo_agente():
    logs = {"pai": _log("penso-pai"), "filho": _log("penso-filho")}
    tool_results = [{"tool_use_id": "t1", "content": "x"}]
    msgs = _agent_thinking.replay_for(logs, "pai", tool_results)
    assert msgs[0]["content"][0]["thinking"] == "penso-pai"
    msgs_filho = _agent_thinking.replay_for(logs, "filho", tool_results)
    assert msgs_filho[0]["content"][0]["thinking"] == "penso-filho"
    # Agente desconhecido: nada — nunca toma emprestado o log de outro.
    assert _agent_thinking.replay_for(logs, "neto", tool_results) == []
    assert _agent_thinking.replay_for({}, "pai", tool_results) == []
    assert _agent_thinking.replay_for(logs, "pai", []) == []


def test_replay_filho_oculto_falha_explicito_sem_contaminar_pai():
    oculto = _store.new_log()
    _store.mark_redacted(oculto, reason="provedor ocultou")
    logs = {"pai": _log("penso-pai"), "filho": oculto}
    with pytest.raises(_replay.ReplayError):
        _agent_thinking.replay_for(logs, "filho", [{"tool_use_id": "t1", "content": "x"}])
    sem_sig = _store.new_log()
    _store.append_delta(sem_sig, thinking="sem assinatura")
    _store.complete(sem_sig)
    with pytest.raises(_replay.ReplayError):
        _agent_thinking.replay_for(
            {"filho": sem_sig}, "filho", [{"tool_use_id": "t1", "content": "x"}]
        )
    # O pai segue intacto e separado.
    msgs = _agent_thinking.replay_for(logs, "pai", [{"tool_use_id": "t1", "content": "x"}])
    assert msgs[0]["content"][0]["thinking"] == "penso-pai"


def test_resposta_do_filho_nunca_vira_pensamento_do_pai():
    bloco = _agent_thinking.child_answer_as_user_content(
        summary="Síntese do filho.", limitations="Sem acesso à escrita."
    )
    assert bloco["role"] == "user"
    dumped = json.dumps(bloco, ensure_ascii=False)
    assert "Síntese do filho." in dumped and "Sem acesso" in dumped
    assert "thinking" not in bloco["content"][0] and "signature" not in dumped


def test_painel_resumo_preservado_e_sem_vazamento():
    log = _log("rascunho interno", display="summarized")
    panel = _store.project_panel(log)
    assert panel["state"] == "done" and panel["summary"] == "rascunho interno"
    snap: dict = {}
    _store.to_snapshot(snap, log)
    assert _store.from_snapshot(snap)["segments"] == log["segments"]
    assert "sig-1" not in json.dumps(panel)  # painel nunca expõe signature
    oculto = _store.new_log()
    _store.mark_redacted(oculto, reason="oculto")
    red = _store.project_panel(oculto)
    assert red["state"] == "redacted" and red["summary"] == ""
