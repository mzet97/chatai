"""Critérios 9–15 (RF-11/12/13, RF-06): streaming, idempotência, retry, erros."""

import httpx2 as httpx
import pytest
from anthropic import (
    APIConnectionError,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
)
from asgiref.sync import sync_to_async

from chat.models import Conversation as Conv
from chat.models import GenerationRun
from chat.services.anthropic_client import classify_error
from chat.services.generation import (
    IdempotencyConflict,
    RunBusy,
    execute_run,
    reserve_run,
)
from tests.fakes import (
    FakeClient,
    FakeFinalMessage,
    FakeMessagesNamespace,
    FakeStream,
    FakeStreamManager,
)

pytestmark = pytest.mark.django_db(transaction=True)
adb = sync_to_async  # ORM síncrono em teste async: await adb(fn)(...)


def _resp(status):
    return httpx.Response(status, request=httpx.Request("POST", "https://x.test"))


def _client_ok(text="Olá, mundo! çãé", **kw):
    final = FakeFinalMessage(text, **kw)
    deltas = [text[:5], text[5:]]
    mgr = FakeStreamManager(FakeStream(deltas, final))
    return FakeClient(FakeMessagesNamespace(stream_manager=mgr))


async def _drain(run_id, user, client):
    return [e async for e in execute_run(run_id, user=user, client=client)]


async def _refresh(run):
    await adb(run.refresh_from_db)()
    return run


async def test_deltas_before_done_and_persisted(user, conversation):
    """Critério 9/10: deltas precedem done; resposta persiste antes do sucesso."""
    run, _, _ = await adb(reserve_run)(
        conversation=conversation, content="oi", idempotency_key="k1"
    )
    events = await _drain(str(run.uuid), user, _client_ok())
    kinds = [e["type"] for e in events]
    assert kinds[0] == "run_started"
    assert "text_delta" in kinds and kinds.index("text_delta") < kinds.index("done")
    assert "".join(e.get("text", "") for e in events if e["type"] == "text_delta")
    await _refresh(run)
    assert run.state == "done" and run.input_tokens == 10 and run.output_tokens == 5
    assert run.actual_model == "fake-model"
    text = await adb(lambda: run.assistant_message.text)()
    assert text.startswith("Olá")


async def test_idempotent_replay_and_conflict(user, conversation):
    """Critério 12: mesma chave retorna estado existente; conteúdo distinto = 409."""
    run1, created1, _ = await adb(reserve_run)(
        conversation=conversation, content="oi", idempotency_key="k"
    )
    assert created1 is True
    run2, created2, _ = await adb(reserve_run)(
        conversation=conversation, content="oi", idempotency_key="k"
    )
    assert (created2, run2.pk) == (False, run1.pk)
    with pytest.raises(IdempotencyConflict):
        await adb(reserve_run)(conversation=conversation, content="outra", idempotency_key="k")


async def test_single_active_run_per_conversation(user, conversation):
    """Critério 12: segunda reserva com a primeira ativa → RunBusy."""
    await adb(reserve_run)(conversation=conversation, content="a", idempotency_key="k1")
    with pytest.raises(RunBusy):
        await adb(reserve_run)(conversation=conversation, content="b", idempotency_key="k2")
    # Após concluir, a exclusividade libera.
    run = await adb(GenerationRun.objects.get)(idempotency_key="k1")
    await _drain(str(run.uuid), user, _client_ok())
    _, created, _ = await adb(reserve_run)(
        conversation=conversation, content="b", idempotency_key="k2"
    )
    assert created is True


async def test_retry_reuses_question(user, conversation, logged_client):
    """Critério 13: retry não duplica a pergunta; registra nova tentativa."""
    run, _, _ = await adb(reserve_run)(
        conversation=conversation, content="pergunta?", idempotency_key="k1"
    )
    await _drain(str(run.uuid), user, _client_ok())
    await adb(_fail_run)(run)
    msg = await adb(lambda: run.user_message)()

    def _post():
        return logged_client.post(
            f"/api/conversations/{conversation.uuid}/messages/{msg.uuid}/retry",
            data={"idempotency_key": "retry-1"},
            content_type="application/json",
        )

    r = await adb(_post)()
    assert r.status_code == 201 and r.json()["attempt"] == 2
    n = await adb(_count_user_messages)(conversation)
    assert n == 1


def _fail_run(run):
    run.state = "failed"
    run.save()
    Conv.objects.filter(pk=run.conversation_id).update(active_run=None)


def _count_user_messages(conversation):
    return conversation.messages.filter(role="user").count()


async def test_error_mapping_and_midstream_error(user, conversation):
    """Critério 14: 401/404/429/conexão classificados; erro no meio do stream persiste."""
    assert (
        classify_error(AuthenticationError("a", response=_resp(401), body={}))[0] == "unauthorized"
    )
    assert (
        classify_error(NotFoundError("n", response=_resp(404), body={}))[0] == "model_unavailable"
    )
    assert classify_error(RateLimitError("r", response=_resp(429), body={}))[0] == "rate_limited"
    req = httpx.Request("POST", "https://x.test")
    assert classify_error(APIConnectionError(request=req))[0] == "api_connection"

    mgr = FakeStreamManager(FakeStream(["parcial…"], FakeFinalMessage("x")))

    async def failing():
        yield "parcial…"
        raise AuthenticationError("bad", response=_resp(401), body={})

    mgr._stream.text_stream = failing()
    run, _, _ = await adb(reserve_run)(
        conversation=conversation, content="oi", idempotency_key="ke"
    )
    events = await _drain(
        str(run.uuid), user, FakeClient(FakeMessagesNamespace(stream_manager=mgr))
    )
    assert events[-1]["type"] == "error" and events[-1]["code"] == "unauthorized"
    await _refresh(run)
    assert run.state == "failed" and run.error_code == "unauthorized"
    text = await adb(lambda: run.assistant_message.text)()
    assert "parcial" in (text or "")


async def test_truncation_flag_and_final_usage_wins(user, conversation):
    """Critério 15: max_tokens → truncamento sinalizado; uso final substitui parcial."""
    run, _, _ = await adb(reserve_run)(
        conversation=conversation, content="oi", idempotency_key="kt"
    )
    events = await _drain(str(run.uuid), user, _client_ok("texto longo", stop_reason="max_tokens"))
    done = events[-1]
    assert done["type"] == "done" and done["truncated"] is True
    await _refresh(run)
    assert run.truncated is True and run.stop_reason == "max_tokens"
    assert (run.input_tokens, run.output_tokens) == (10, 5)  # finais, não soma


async def test_failed_run_releases_lock_even_on_count_failure(user, conversation):
    """Toda saída libera a exclusividade (RF-13): contagem falha → novo envio possível."""
    msgs = FakeMessagesNamespace(
        stream_manager=FakeStreamManager(FakeStream([], FakeFinalMessage("x")))
    )

    async def failing_count(*, model, system, messages):
        raise RuntimeError("count quebrou")

    msgs.count_tokens = failing_count
    run, _, _ = await adb(reserve_run)(
        conversation=conversation, content="oi", idempotency_key="kc"
    )
    events = await _drain(str(run.uuid), user, FakeClient(msgs))
    assert events[-1]["code"] == "count_failed"
    _, created, _ = await adb(reserve_run)(
        conversation=conversation, content="oi2", idempotency_key="kc2"
    )
    assert created is True
