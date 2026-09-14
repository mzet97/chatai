"""Nível de variação: persistência, validação, payload real e snapshots."""

import json

import pytest
from asgiref.sync import sync_to_async

from chat.models import Conversation as Conv
from chat.models import GenerationRun
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


def test_patch_valido_persiste_e_recarrega(logged_client, conversation):
    r = _patch(logged_client, conversation.uuid, {"temperature_level": "high"})
    assert r.status_code == 200
    assert r.json()["temperature_level"] == "high"
    assert Conv.objects.get(pk=conversation.pk).temperature_level == "high"
    detail = logged_client.get(f"/api/conversations/{conversation.uuid}").json()
    assert detail["temperature_level"] == "high"


def test_patch_invalido_e_temperatura_numerica_rejeitados(logged_client, conversation):
    bad = {"temperature_level": "turbo"}
    assert _patch(logged_client, conversation.uuid, bad).status_code == 400
    assert _patch(logged_client, conversation.uuid, {"temperature": 999}).status_code == 400
    assert _patch(logged_client, conversation.uuid, {"temperature": 0.5}).status_code == 400
    assert Conv.objects.get(pk=conversation.pk).temperature_level == "medium"


def test_isolamento_entre_usuarios(client, user2, conversation):
    client.force_login(user2)
    assert client.patch(
        f"/api/conversations/{conversation.uuid}",
        data=json.dumps({"temperature_level": "low"}),
        content_type="application/json",
    ).status_code == 404


def test_patch_durante_geracao_ativa_retorna_409(logged_client, conversation):
    from chat.services.generation import reserve_run

    reserve_run(conversation=conversation, content="oi", idempotency_key="k409")
    r = _patch(logged_client, conversation.uuid, {"temperature_level": "low"})
    assert r.status_code == 409
    assert r.json()["temperature_level"] == "medium"
    assert "concluir ou interromper" in r.json()["message"]


def test_support_por_modelo_no_detalhe(logged_client, conversation):
    def support(model):
        logged_client.patch(
            f"/api/conversations/{conversation.uuid}",
            data=json.dumps({"preferred_model": model}),
            content_type="application/json",
        )
        return logged_client.get(f"/api/conversations/{conversation.uuid}").json()[
            "temperature_support"
        ]

    assert support("claude-haiku-4-5-20251001")["state"] == "supported"
    s = support("claude-sonnet-5")
    assert s["state"] == "unsupported" and "não está disponível" in s["reason"]
    s = support("modelo-que-nao-existe")
    assert s["state"] == "unknown" and "ainda não confirmado" in s["reason"]


def test_troca_de_modelo_preserva_preferencia(logged_client, conversation):
    _patch(logged_client, conversation.uuid, {"temperature_level": "high"})
    _patch(logged_client, conversation.uuid, {"preferred_model": "claude-sonnet-5"})
    d = logged_client.get(f"/api/conversations/{conversation.uuid}").json()
    assert d["temperature_level"] == "high"
    assert d["temperature_support"]["state"] == "unsupported"
    _patch(logged_client, conversation.uuid, {"preferred_model": "claude-haiku-4-5-20251001"})
    d = logged_client.get(f"/api/conversations/{conversation.uuid}").json()
    assert d["temperature_level"] == "high"
    assert d["temperature_support"]["state"] == "supported"


def test_patch_nao_gera_execucao(logged_client, conversation):
    _patch(logged_client, conversation.uuid, {"temperature_level": "low"})
    assert GenerationRun.objects.filter(conversation=conversation).count() == 0


async def _run_with(user, conversation, level, model):
    from chat.services.generation import execute_run, reserve_run

    def _prep():
        conversation.temperature_level = level
        conversation.preferred_model = model
        conversation.save()
        key = f"k-{level}-{model}"
        return reserve_run(conversation=conversation, content="oi", idempotency_key=key)

    run, _, _ = await adb(_prep)()
    client, ns = _client_spy()
    events = [e async for e in execute_run(str(run.uuid), user=user, client=client)]
    assert events[-1]["type"] == "done"
    await adb(run.refresh_from_db)()
    return run, ns


async def test_payload_suportado_envia_temperature(user, conversation):
    run, ns = await _run_with(user, conversation, "low", "claude-haiku-4-5-20251001")
    stream = ns.calls["stream"][0]
    assert stream["extra_body"] == {"temperature": 0.2}
    assert "temperature" not in stream
    assert "system" in stream and "messages" in stream  # contexto/system intactos
    assert all("temperature" not in c for c in ns.calls["count_tokens"])
    assert run.snapshot["requested_level"] == "low"
    assert run.snapshot["temperature_sent"] == 0.2
    assert run.snapshot["temperature_state"] == "supported"
    assert run.snapshot["temp_map_version"] == "temp-map-v1"


async def test_payload_sem_suporte_omite_temperature(user, conversation):
    run, ns = await _run_with(user, conversation, "high", "claude-sonnet-5")
    stream = ns.calls["stream"][0]
    assert "extra_body" not in stream
    assert "temperature" not in stream
    assert run.snapshot["requested_level"] == "high"
    assert run.snapshot["temperature_sent"] is None
    assert run.snapshot["temperature_state"] == "unsupported"
    assert run.snapshot["temperature_reason"]


async def test_payload_desconhecido_omite_sem_inventar(user, conversation):
    run, ns = await _run_with(user, conversation, "medium", "modelo-xyz")
    stream = ns.calls["stream"][0]
    assert "extra_body" not in stream and "temperature" not in stream
    assert "thinking" not in stream  # app nunca envia thinking
    assert run.snapshot["temperature_state"] == "unknown"


async def test_snapshot_antigo_nao_e_reescrito(user, conversation):
    run1, _ = await _run_with(user, conversation, "low", "claude-haiku-4-5-20251001")
    before = dict(run1.snapshot)
    run2, _ = await _run_with(user, conversation, "high", "claude-haiku-4-5-20251001")
    await adb(run1.refresh_from_db)()
    assert run1.snapshot == before
    assert run1.snapshot["requested_level"] == "low"
    assert run2.snapshot["requested_level"] == "high"

    def _legacy():
        return GenerationRun.objects.create(
            conversation=conversation,
            user_message=run1.user_message,
            attempt=99,
            idempotency_key="legacy",
            content_hash="0" * 64,
            snapshot={},
        )

    assert "requested_level" not in (await adb(_legacy)()).snapshot


async def test_retry_usa_snapshot_novo_com_nivel_atual(user, conversation, logged_client):
    from chat.services.generation import execute_run, reserve_run

    run, _, _ = await adb(reserve_run)(
        conversation=conversation, content="pergunta?", idempotency_key="kr"
    )
    client, _ = _client_spy()
    events = [e async for e in execute_run(str(run.uuid), user=user, client=client)]
    assert events[-1]["type"] == "done"
    await adb(_fail)(run)
    msg = await adb(lambda: run.user_message)()
    conversation.temperature_level = "high"
    await adb(conversation.save)()

    def _post():
        return logged_client.post(
            f"/api/conversations/{conversation.uuid}/messages/{msg.uuid}/retry",
            data=json.dumps({"idempotency_key": "retry-2"}),
            content_type="application/json",
        )

    r = await adb(_post)()
    assert r.status_code in (200, 201)
    new_run = await adb(GenerationRun.objects.get)(uuid=r.json()["run_id"])
    assert new_run.attempt == run.attempt + 1
    # Snapshot nasce na execução: drena e só então verifica o nível vigente.
    client2, _ = _client_spy()
    from chat.services.generation import execute_run as _exec

    events = [e async for e in _exec(str(new_run.uuid), user=user, client=client2)]
    assert events[-1]["type"] == "done"
    await adb(new_run.refresh_from_db)()
    assert new_run.snapshot["requested_level"] == "high"


def _fail(run):
    run.state = "failed"
    run.save()
    Conv.objects.filter(pk=run.conversation_id).update(active_run=None)
