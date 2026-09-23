"""Pensamento por conversa: PATCH, suporte, payload e bloqueio de conflito."""

import json

import pytest
from asgiref.sync import sync_to_async

from chat.models import Conversation as Conv
from chat.services import thinking
from tests.fakes import (
    FakeClient,
    FakeFinalMessage,
    FakeMessagesNamespace,
    FakeStream,
    FakeStreamManager,
)

pytestmark = pytest.mark.django_db(transaction=True)
adb = sync_to_async


def _patch(client, uuid, body):
    return client.patch(
        f"/api/conversations/{uuid}", data=json.dumps(body), content_type="application/json"
    )


def _client_spy(text="ok"):
    ns = FakeMessagesNamespace(
        stream_manager=FakeStreamManager(FakeStream([text], FakeFinalMessage(text)))
    )
    return FakeClient(ns), ns


def test_patch_persiste_e_valida(logged_client, conversation):
    r = _patch(
        logged_client,
        conversation.uuid,
        {
            "thinking_mode": "enabled",
            "thinking_level": "high",
            "thinking_budget": 2048,
            "thinking_show_summary": True,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["thinking_mode"] == "enabled"
    assert data["thinking_level"] == "high"
    assert data["thinking_budget"] == 2048
    assert data["thinking_show_summary"] is True
    assert _patch(logged_client, conversation.uuid, {"thinking_mode": "turbo"}).status_code == 400
    assert _patch(logged_client, conversation.uuid, {"thinking_budget": 999}).status_code == 400
    kept = Conv.objects.get(pk=conversation.pk)
    assert (kept.thinking_mode, kept.thinking_budget) == ("enabled", 2048)


def test_controle_pensamento_renderizado(logged_client):
    html = logged_client.get("/").content.decode()
    for token in (
        "thinking-btn",
        "thinking-pop",
        "think-mode-enabled",
        "thinking-level",
        "thinking-budget",
        "thinking-summary",
        "thinking-ui.js",
    ):
        assert token in html


def test_padrao_e_suporte_no_detalhe(logged_client, conversation):
    d = logged_client.get(f"/api/conversations/{conversation.uuid}").json()
    assert d["thinking_mode"] == "default"
    assert d["thinking_level"] == "medium"
    assert d["thinking_support"]["capability"] == "unknown"
    assert "vision" in d["thinking_support"]


def test_patch_durante_geracao_ativa_retorna_409(logged_client, conversation):
    from chat.services.generation import reserve_run

    reserve_run(conversation=conversation, content="oi", idempotency_key="k409t")
    r = _patch(logged_client, conversation.uuid, {"thinking_mode": "enabled"})
    assert r.status_code == 409
    assert r.json()["thinking_mode"] == "default"


def _hook_caps(monkeypatch, **caps):
    real = thinking.resolve_thinking

    def hooked(mode, level, **kw):
        # Une conjuntos (não setdefault): o teste pode injetar caps e
        # _run_with injeta os padrões; aninhados, ambos precisam valer.
        for k, v in caps.items():
            kw[k] = set(kw.get(k) or set()) | set(v)
        return real(mode, level, **kw)

    monkeypatch.setattr(thinking, "resolve_thinking", hooked)


async def _run_with(user, conversation, monkeypatch, **prefs):
    from chat.services.generation import execute_run, reserve_run

    _hook_caps(monkeypatch, adaptive={"m-adapt-t"}, legacy={"m-legacy-t"})

    def _prep():
        for k, v in prefs.items():
            setattr(conversation, k, v)
        conversation.save()
        return reserve_run(
            conversation=conversation,
            content="oi",
            idempotency_key=f"k-{id(conversation)}-{prefs.get('thinking_mode')}",
        )

    run, _, _ = await adb(_prep)()
    client, ns = _client_spy()
    events = [e async for e in execute_run(str(run.uuid), user=user, client=client)]
    await adb(run.refresh_from_db)()
    return run, ns, events


async def test_adaptativo_envia_thinking_e_omite_temperatura(user, conversation, monkeypatch):
    # Modelo com temperatura suportada + capacidade adaptativa injetada:
    # pensamento vence, temperatura sai sem perder a preferência.
    _hook_caps(monkeypatch, adaptive={"claude-haiku-4-5-20251001"})
    conversation.temperature_level = "low"
    conversation.preferred_model = "claude-haiku-4-5-20251001"
    await adb(conversation.save)()
    run, ns, events = await _run_with(
        user,
        conversation,
        monkeypatch,
        thinking_mode="enabled",
        thinking_level="high",
        thinking_show_summary=True,
    )
    assert events[-1]["type"] == "done"
    stream = ns.calls["stream"][0]
    assert stream["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert stream["output_config"] == {"effort": "high"}
    assert "extra_body" not in stream  # temperatura omitida, preferência preservada
    assert run.snapshot["thinking_state"] == "adaptive"
    assert run.snapshot["thinking_sent"] is True
    assert run.snapshot["temperature_not_applied"] == "thinking"


async def test_desconhecido_omite_sem_chamar_pensamento(user, conversation, monkeypatch):
    conversation.preferred_model = "modelo-xyz"
    await adb(conversation.save)()
    run, ns, events = await _run_with(user, conversation, monkeypatch, thinking_mode="enabled")
    assert events[-1]["type"] == "done"
    stream = ns.calls["stream"][0]
    assert "thinking" not in stream and "output_config" not in stream
    assert run.snapshot["thinking_state"] == "unknown"


async def test_conflito_bloqueia_antes_da_chamada(user, conversation, monkeypatch):
    conversation.preferred_model = "m-legacy-t"
    conversation.max_output_tokens = 1024
    await adb(conversation.save)()
    run, ns, events = await _run_with(
        user,
        conversation,
        monkeypatch,
        thinking_mode="enabled",
        thinking_budget=2048,
    )
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == "thinking_conflict"
    assert ns.calls["stream"] == []  # nenhuma chamada paga
    await adb(run.refresh_from_db)()
    assert run.state == "failed"
