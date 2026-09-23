"""M2: execução durável — comandos, diário, cancelamento, retomada.

Sem chamadas pagas: geração via FakeClient (tests/fakes.py). Sem commit.
Cobre: POST comando 202 idempotente; 409 em conteúdo divergente; worker
local reivindica (CAS) e diário com seq monotônica; eventos com cursor sem
duplicar; cancel idempotente; resume de interrupted; isolamento por dono.
"""

import json

import pytest
from asgiref.sync import sync_to_async

from chat.models import Conversation, GenerationRun
from chat.models_runtime import RunJournalEvent

pytestmark = pytest.mark.django_db(transaction=True)
adb = sync_to_async


async def _user():
    from django.contrib.auth import get_user_model

    return await adb(get_user_model().objects.create_user)("m2user", password="pw")


def _post(client, conv, text="oi", key="k"):
    url = f"/api/conversations/{conv.uuid}/runs"
    body = {"text": text, "idempotency_key": key}
    return client.post(url, data=json.dumps(body), content_type="application/json")


@pytest.mark.asyncio
async def test_command_idempotent_and_conflict(async_client):
    user = await _user()
    await adb(async_client.force_login)(user)
    conv = await adb(Conversation.objects.create)(owner=user, title="t")
    r1 = await _post(async_client, conv, text="olá?", key="k1")
    assert r1.status_code == 202
    run_id = r1.json()["run_id"]
    r2 = await _post(async_client, conv, text="olá?", key="k1")
    assert r2.status_code == 202
    assert r2.json()["run_id"] == run_id  # replay: mesmo run, sem duplicar
    assert await adb(GenerationRun.objects.filter(conversation=conv).count)() == 1
    r3 = await _post(async_client, conv, text="outra", key="k1")
    assert r3.status_code == 409  # mesma chave, conteúdo diferente


@pytest.mark.asyncio
async def test_worker_claims_and_journals_monotonic(async_client):
    from chat.services.runtime import claims, journal

    user = await _user()
    await adb(async_client.force_login)(user)
    conv = await adb(Conversation.objects.create)(owner=user, title="t")
    r = await _post(async_client, conv, key="k2")
    run_id = r.json()["run_id"]
    claimed = await adb(claims.claim_next)(worker_id="w1")
    assert claimed is not None and str(claimed.uuid) == run_id
    assert await adb(claims.claim_next)(worker_id="w2") is None  # sem dupla reivindicação
    await adb(journal.append)(claimed, "run_started", {"state": "running"})
    await adb(journal.append)(claimed, "text_delta", {"text": "olá"})
    await adb(journal.append)(claimed, "done", {"state": "done"})
    got = await adb(
        lambda: list(
            RunJournalEvent.objects.filter(run=claimed).order_by("seq").values_list("seq", "kind")
        )
    )()
    assert [s for s, _ in got] == [1, 2, 3, 4]
    assert got[0][1] == "run_queued"  # comando já abre o diário


@pytest.mark.asyncio
async def test_events_cursor_no_duplicates(async_client):
    from chat.services.runtime import journal

    user = await _user()
    await adb(async_client.force_login)(user)
    conv = await adb(Conversation.objects.create)(owner=user, title="t")
    r = await _post(async_client, conv, key="k3")
    run = await adb(GenerationRun.objects.get)(uuid=r.json()["run_id"])
    await adb(journal.append)(run, "a", {})
    await adb(journal.append)(run, "b", {})
    e1 = await adb(journal.read)(run, after=0)
    e2 = await adb(journal.read)(run, after=1)
    assert [e["seq"] for e in e1] == [1, 2, 3]  # 1 = run_queued do comando
    assert [e["seq"] for e in e2] == [2, 3]
    assert [e["seq"] for e in await adb(journal.read)(run, after=3)] == []


@pytest.mark.asyncio
async def test_cancel_idempotent(async_client):
    user = await _user()
    await adb(async_client.force_login)(user)
    conv = await adb(Conversation.objects.create)(owner=user, title="t")
    r = await _post(async_client, conv, key="k4")
    url = f"/api/runs/{r.json()['run_id']}/cancel"
    c1 = await async_client.post(url)
    c2 = await async_client.post(url)
    assert c1.status_code == 200 and c2.status_code == 200
    assert c1.json()["cancelled"] is True
    assert c2.json()["cancelled"] in (True, False)  # repetir não é erro
    run = await adb(GenerationRun.objects.get)(uuid=r.json()["run_id"])
    assert run.state in ("cancelled", "cancel_requested", "running", "queued")


@pytest.mark.asyncio
async def test_resume_from_interrupted(async_client):
    user = await _user()
    await adb(async_client.force_login)(user)
    conv = await adb(Conversation.objects.create)(owner=user, title="t")
    r = await _post(async_client, conv, key="k5")
    run = await adb(GenerationRun.objects.get)(uuid=r.json()["run_id"])
    await adb(GenerationRun.objects.filter(pk=run.pk).update)(state="interrupted")
    ok = await async_client.post(f"/api/runs/{r.json()['run_id']}/resume")
    assert ok.status_code == 202
    run = await adb(GenerationRun.objects.get)(uuid=r.json()["run_id"])
    assert run.state == "queued"
    bad = await async_client.post(f"/api/runs/{r.json()['run_id']}/resume")
    assert bad.status_code == 409  # queued não retoma


@pytest.mark.asyncio
async def test_events_sse_replays_journal_and_dones(async_client):
    from chat.services.runtime import journal

    user = await _user()
    await adb(async_client.force_login)(user)
    conv = await adb(Conversation.objects.create)(owner=user, title="t")
    r = await _post(async_client, conv, key="k7")
    run = await adb(GenerationRun.objects.get)(uuid=r.json()["run_id"])
    await adb(journal.append)(run, "text_delta", {"text": "oi"})
    await adb(GenerationRun.objects.filter(pk=run.pk).update)(state="done")
    resp = await async_client.get(f"/api/runs/{r.json()['run_id']}/events?after=0")
    assert resp.status_code == 200
    assert "text/event-stream" in resp["Content-Type"]
    body = b"".join([c async for c in resp.streaming_content]).decode()
    assert "run_queued" in body and "text_delta" in body
    assert '"type": "done"' in body or "done" in body
    # Reconexão com cursor não repete o passado.
    resp2 = await async_client.get(f"/api/runs/{r.json()['run_id']}/events?after=2")
    body2 = b"".join([c async for c in resp2.streaming_content]).decode()
    assert "run_queued" not in body2 and "text_delta" not in body2


@pytest.mark.asyncio
async def test_cross_user_run_is_hidden(async_client):
    from django.contrib.auth import get_user_model

    u1 = await adb(get_user_model().objects.create_user)("u1", password="pw")
    u2 = await adb(get_user_model().objects.create_user)("u2", password="pw")
    await adb(async_client.force_login)(u1)
    conv = await adb(Conversation.objects.create)(owner=u1, title="t")
    r = await _post(async_client, conv, key="k6")
    await adb(async_client.force_login)(u2)
    assert (await async_client.get(f"/api/runs/{r.json()['run_id']}")).status_code == 404
    assert (await async_client.post(f"/api/runs/{r.json()['run_id']}/cancel")).status_code == 404
