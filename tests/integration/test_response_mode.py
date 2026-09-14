"""Modo de entrega por conversa: streaming x completa (SDD Streaming).

Contrato: ambos os modos usam o MESMO client.messages.stream do provedor;
o modo controla só a entrega ao navegador. `done` é canônico (carrega o
texto final persistido). Todo evento de dados tem run_id + seq monotônica.
"""

import asyncio
import json

import pytest
from asgiref.sync import sync_to_async
from django.test import RequestFactory

import chat.services.generation as gen
import chat.views.api_runs as api_runs
from chat.models import Conversation as Conv
from chat.models import GenerationRun
from chat.services.generation import execute_run, reserve_run
from tests.fakes import (
    FakeClient,
    FakeFinalMessage,
    FakeMessagesNamespace,
    FakeStream,
    FakeStreamManager,
)

pytestmark = pytest.mark.django_db(transaction=True)
adb = sync_to_async


class GatedStream(FakeStream):
    """Libera o delta `gate_index` só após `gate.set()` (sincronia controlada)."""

    def __init__(self, *args, gate=None, gate_index=1, **kwargs):
        super().__init__(*args, **kwargs)
        self._gate = gate
        self._gate_index = gate_index

    @property
    def text_stream(self):
        return self._gated()

    async def _gated(self):
        for i, d in enumerate(self._deltas):
            if self._gate is not None and i == self._gate_index:
                await self._gate.wait()
            yield d


def _client(text="Resposta final.", deltas=None, gate=None, gate_index=1, **kw):
    final = FakeFinalMessage(text, **kw)
    stream = GatedStream(
        deltas if deltas is not None else [text], final, gate=gate, gate_index=gate_index
    )
    return FakeClient(FakeMessagesNamespace(stream_manager=FakeStreamManager(stream)))


async def _reserve(conversation, content="oi", key="k1"):
    return await adb(reserve_run)(conversation=conversation, content=content, idempotency_key=key)


async def _drain(run_id, user, client):
    return [e async for e in execute_run(run_id, user=user, client=client)]


def _patch(client, uuid, body):
    return client.patch(
        f"/api/conversations/{uuid}", data=json.dumps(body), content_type="application/json"
    )


# ---------- preferência (PATCH) ----------


def test_patch_modo_persiste_e_recarrega(logged_client, conversation):
    assert logged_client.get(f"/api/conversations/{conversation.uuid}").json()["response_mode"] == (
        "streaming"
    )
    r = _patch(logged_client, conversation.uuid, {"response_mode": "complete"})
    assert r.status_code == 200 and r.json()["response_mode"] == "complete"
    assert Conv.objects.get(pk=conversation.pk).response_mode == "complete"
    detail = logged_client.get(f"/api/conversations/{conversation.uuid}").json()
    assert detail["response_mode"] == "complete"


def test_patch_modo_invalido_rejeitado(logged_client, conversation):
    assert _patch(logged_client, conversation.uuid, {"response_mode": "turbo"}).status_code == 400
    assert Conv.objects.get(pk=conversation.pk).response_mode == "streaming"


def test_patch_modo_nao_gera_execucao(logged_client, conversation):
    _patch(logged_client, conversation.uuid, {"response_mode": "complete"})
    assert GenerationRun.objects.filter(conversation=conversation).count() == 0
    assert conversation.messages.count() == 0


def test_patch_modo_durante_geracao_ativa_retorna_409(logged_client, conversation):
    reserve_run(conversation=conversation, content="oi", idempotency_key="k409m")
    r = _patch(logged_client, conversation.uuid, {"response_mode": "complete"})
    assert r.status_code == 409
    assert r.json()["response_mode"] == "streaming"
    assert Conv.objects.get(pk=conversation.pk).response_mode == "streaming"


def test_modo_isolado_entre_conversas_e_usuarios(logged_client, client, user2, conversation):
    conv2 = Conv.objects.create(owner=conversation.owner, title="outra")
    _patch(logged_client, conversation.uuid, {"response_mode": "complete"})
    assert Conv.objects.get(pk=conv2.pk).response_mode == "streaming"
    client.force_login(user2)
    assert (
        client.patch(
            f"/api/conversations/{conversation.uuid}",
            data=json.dumps({"response_mode": "complete"}),
            content_type="application/json",
        ).status_code
        == 404
    )


def test_stream_anonimo_nao_vaza(client, conversation):
    r = client.get("/api/runs/00000000-0000-0000-0000-000000000000/stream")
    assert r.status_code in (302, 403)  # login exigido, sem conteúdo


# ---------- modo completo: sem deltas na rede, done canônico ----------


async def test_complete_sem_text_delta_e_done_canonico(user, conversation):
    conversation.response_mode = "complete"
    await adb(conversation.save)()
    run, _, _ = await _reserve(conversation)
    events = await _drain(str(run.uuid), user, _client("Olá, completa!"))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "run_started"
    assert "text_delta" not in kinds
    assert kinds[-1] == "done"
    started = events[0]
    assert started["mode"] == "complete" and started["requested_mode"] == "complete"
    assert started["conversation_id"] == str(conversation.uuid)
    done = events[-1]
    assert done["text"] == "Olá, completa!"
    await adb(run.refresh_from_db)()
    assert run.state == "done"
    assert run.snapshot["requested_response_mode"] == "complete"
    assert run.snapshot["effective_response_mode"] == "complete"
    text = await adb(lambda: run.assistant_message.text)()
    assert text == done["text"]  # done confirma o que foi persistido, sem duplicar


async def test_envelope_run_id_e_seq_em_todos_os_eventos(user, conversation):
    run, _, _ = await _reserve(conversation)
    events = await _drain(str(run.uuid), user, _client("abc"))
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert all(e["run_id"] == str(run.uuid) for e in events)
    assert events[-1]["type"] == "done"


async def test_mesmo_contexto_nos_dois_modos(user, conversation):
    """Uma chamada de provedor por tentativa; contexto idêntico, só a entrega muda."""
    from tests.fakes import FakeMessagesNamespace as NS

    kwargs_by_mode = {}
    for mode, key in (("streaming", "km1"), ("complete", "km2")):
        conversation.response_mode = mode
        await adb(conversation.save)()
        run, _, _ = await _reserve(conversation, content="mesma pergunta", key=key)
        ns = NS(stream_manager=FakeStreamManager(FakeStream(["t"], FakeFinalMessage("t"))))
        events = await _drain(str(run.uuid), user, FakeClient(ns))
        assert len(ns.calls["stream"]) == 1
        assert events[-1]["type"] == "done"
        kwargs_by_mode[mode] = ns.calls["stream"][0]
    a, b = kwargs_by_mode["streaming"], kwargs_by_mode["complete"]
    # O histórico cresce (run1 concluído entra no contexto do run2); a pergunta
    # atual aparece exatamente 1x em ambos e o resto é o turno anterior completo.
    assert a["messages"] == [{"role": "user", "content": "mesma pergunta"}]
    assert b["messages"] == a["messages"] + [
        {"role": "assistant", "content": "t"},
        {"role": "user", "content": "mesma pergunta"},
    ]
    assert a["system"] == b["system"] and a["max_tokens"] == b["max_tokens"]
    assert a["model"] == b["model"]


async def test_snapshot_congela_modo_da_execucao(user, conversation):
    conversation.response_mode = "complete"
    await adb(conversation.save)()
    run1, _, _ = await _reserve(conversation, key="ks1")
    await _drain(str(run1.uuid), user, _client("um"))
    conversation.response_mode = "streaming"
    await adb(conversation.save)()
    run2, _, _ = await _reserve(conversation, content="outra", key="ks2")
    await _drain(str(run2.uuid), user, _client("dois"))
    await adb(run1.refresh_from_db)()
    await adb(run2.refresh_from_db)()
    assert run1.snapshot["effective_response_mode"] == "complete"
    assert run2.snapshot["effective_response_mode"] == "streaming"


# ---------- incremental de verdade (view + gate, sem sleep frágil) ----------


async def _view_response(user, run, factory, monkeypatch, heartbeat=None):
    # monkeypatch (teardown no fim do teste): o factory é lido pelo gerador
    # event_source de forma lazy, então restaurar cedo chamaria a API real.
    monkeypatch.setattr(api_runs, "CLIENT_FACTORY", factory)
    if heartbeat is not None:
        monkeypatch.setattr(api_runs, "HEARTBEAT_SECONDS", heartbeat)
    request = RequestFactory().get(f"/api/runs/{run.uuid}/stream")
    request.user = user

    async def _auser():
        return user

    request.auser = _auser  # login_required em view async usa request.auser()
    return await api_runs.run_stream(request, str(run.uuid))


def _sse_payloads(raw: str):
    return [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]


async def test_view_entrega_delta_antes_de_concluir(user, conversation, monkeypatch):
    """Streaming ligado: fragmento chega ao 'navegador' com o provedor travado."""
    gate = asyncio.Event()
    run, _, _ = await _reserve(conversation)
    resp = await _view_response(
        user,
        run,
        lambda u, r: _client("AAAABBBB", deltas=["AAAA", "BBBB"], gate=gate),
        monkeypatch,
    )
    assert resp["Content-Type"] == "text/event-stream; charset=utf-8"
    assert resp["Cache-Control"] == "no-store, no-transform"
    seen = ""
    done_seen = False
    async for chunk in resp.streaming_content:
        seen += chunk.decode() if isinstance(chunk, bytes) else chunk
        payloads = _sse_payloads(seen)
        if any(p.get("type") == "text_delta" for p in payloads):
            break
    assert not gate.is_set()  # provedor ainda travado no 2º delta
    assert not done_seen
    deltas = "".join(
        p.get("text", "") for p in _sse_payloads(seen) if p.get("type") == "text_delta"
    )
    assert deltas == "AAAA"  # primeiro fragmento real, não simulação
    gate.set()
    async for chunk in resp.streaming_content:
        seen += chunk.decode() if isinstance(chunk, bytes) else chunk
    payloads = _sse_payloads(seen)
    assert payloads[-1]["type"] == "done" and payloads[-1]["text"] == "AAAABBBB"


async def test_view_heartbeat_sem_matar_a_geracao(user, conversation, monkeypatch):
    """Upstream quieto: comentários ping, mesma geração viva até o fim."""
    gate = asyncio.Event()
    run, _, _ = await _reserve(conversation)
    resp = await _view_response(
        user,
        run,
        lambda u, r: _client("tarde", deltas=["tarde"], gate=gate, gate_index=0),
        monkeypatch,
        heartbeat=0.05,
    )
    seen = ""
    pings = 0
    async for chunk in resp.streaming_content:
        seen += chunk.decode() if isinstance(chunk, bytes) else chunk
        pings = seen.count(": ping")
        if pings >= 1:
            break
    assert ": ping" in seen  # heartbeat não é texto do assistente
    gate.set()
    async for chunk in resp.streaming_content:
        seen += chunk.decode() if isinstance(chunk, bytes) else chunk
    payloads = _sse_payloads(seen)
    assert payloads[-1]["type"] == "done" and payloads[-1]["text"] == "tarde"


# ---------- cancelamento / erros / recuperação ----------


async def test_cancel_durante_resposta_emite_cancelled(user, conversation):
    gate = asyncio.Event()
    run, _, _ = await _reserve(conversation)
    client = _client("parcial…fim", deltas=["parcial…", "fim"], gate=gate)
    agen = execute_run(str(run.uuid), user=user, client=client)
    first = await agen.__anext__()
    assert first["type"] == "run_started"
    second = await agen.__anext__()
    assert second["type"] == "text_delta"
    await adb(gen.request_cancel)(str(run.uuid))
    gate.set()
    rest = [e async for e in agen]
    assert [e["type"] for e in rest] == ["cancelled"]
    await adb(run.refresh_from_db)()
    assert run.state == "cancelled"
    assert client.messages._stream_manager._stream.closed is True  # upstream fechado
    # Exclusividade liberada: nova reserva funciona.
    _, created, _ = await adb(reserve_run)(
        conversation=conversation, content="depois", idempotency_key="k-after"
    )
    assert created is True


async def test_fechar_antes_do_primeiro_delta_interrompe(user, conversation):
    """Sem done não há sucesso: cancela com o gerador aguardando o provedor."""
    gate = asyncio.Event()
    run, _, _ = await _reserve(conversation)
    client = _client("nunca", deltas=["nunca"], gate=gate, gate_index=0)
    agen = execute_run(str(run.uuid), user=user, client=client)
    assert (await agen.__anext__())["type"] == "run_started"
    pending = asyncio.ensure_future(agen.__anext__())
    await asyncio.sleep(0.2)  # gerador parado no gate do provedor, antes do 1º delta
    pending.cancel()
    try:
        await pending
    except (asyncio.CancelledError, StopAsyncIteration):
        pass
    await agen.aclose()
    await adb(run.refresh_from_db)()
    assert run.state == "interrupted"
    assert await adb(lambda: Conv.objects.get(pk=conversation.pk).active_run_id)() is None


async def test_cancel_tardio_nao_sobrescreve_sucesso(user, conversation):
    run, _, _ = await _reserve(conversation)
    events = await _drain(str(run.uuid), user, _client("ok"))
    assert events[-1]["type"] == "done"
    assert await adb(gen.request_cancel)(str(run.uuid)) is False
    await adb(run.refresh_from_db)()
    assert run.state == "done"


async def test_persist_failed_recuperavel(user, conversation, monkeypatch):
    orig = gen._finalize

    def flaky(run_id, **kw):
        if kw.get("state") == "done":
            raise RuntimeError("disco cheio")
        return orig(run_id, **kw)

    monkeypatch.setattr(gen, "_finalize", flaky)
    run, _, _ = await _reserve(conversation)
    events = await _drain(str(run.uuid), user, _client("texto"))
    assert events[-1]["type"] == "error" and events[-1]["code"] == "persist_failed"
    await adb(run.refresh_from_db)()
    assert run.state == "failed" and run.error_code == "persist_failed"


async def test_complete_erro_no_meio_nao_vaza_parcial(user, conversation):
    import httpx2 as httpx
    from anthropic import AuthenticationError

    conversation.response_mode = "complete"
    await adb(conversation.save)()
    mgr = FakeStreamManager(FakeStream(["segredo-parcial"], FakeFinalMessage("x")))
    resp = httpx.Response(401, request=httpx.Request("POST", "https://x.test"))

    async def failing():
        yield "segredo-parcial"
        raise AuthenticationError("bad", response=resp, body={})

    mgr._stream.text_stream = failing()
    run, _, _ = await _reserve(conversation)
    client = FakeClient(FakeMessagesNamespace(stream_manager=mgr))
    events = await _drain(str(run.uuid), user, client)
    assert [e["type"] for e in events] == ["run_started", "error"]
    assert "segredo-parcial" not in json.dumps(events)  # nada parcial na rede
    assert events[-1]["code"] == "unauthorized"


async def test_html_malicioso_nao_sanitizado_no_backend_mas_sem_segredo(user, conversation):
    """Backend transporta o texto fiel; sanitização é do renderer Markdown no frontend."""
    raw = "<script>alert(1)</script> **oi**"
    run, _, _ = await _reserve(conversation)
    events = await _drain(str(run.uuid), user, _client(raw))
    assert events[-1]["text"] == raw
    await adb(run.refresh_from_db)()
    assert "api_key" not in json.dumps(run.snapshot)
    assert run.snapshot.get("api_key") is None


async def test_idempotencia_duplo_envio_uma_execucao(user, conversation):
    run1, c1, _ = await _reserve(conversation, key="dup")
    run2, c2, _ = await _reserve(conversation, key="dup")
    assert (c1, c2, run1.pk) == (True, False, run2.pk)
    await _drain(str(run1.uuid), user, _client("ok"))
    n = await adb(lambda: GenerationRun.objects.filter(conversation=conversation).count())()
    assert n == 1
