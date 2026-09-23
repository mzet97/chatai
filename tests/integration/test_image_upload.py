"""M3 integrado: endpoint multipart, envio com imagens e blocos no payload."""

import base64
import io
import json

import pytest
from asgiref.sync import sync_to_async
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from chat.services.generation import execute_run, reserve_run
from chat.services.images import MAX_IMAGE_BYTES
from tests.fakes import (
    FakeClient,
    FakeFinalMessage,
    FakeMessagesNamespace,
    FakeStream,
    FakeStreamManager,
)

pytestmark = pytest.mark.django_db(transaction=True)
adb = sync_to_async


def _png(size=(96, 72), color=(20, 90, 160)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _upload(client, content, name="foto.png"):
    return client.post("/api/images", {"file": SimpleUploadedFile(name, content)})


def test_endpoint_retorna_normalizada_e_miniatura(logged_client):
    r = _upload(logged_client, _png())
    assert r.status_code == 201
    body = r.json()
    assert body["media_type"] == "image/png"
    assert (body["width"], body["height"]) == (96, 72)
    assert body["thumbnail"].startswith("data:image/png;base64,")
    assert base64.b64decode(body["data"])  # base64 válido da variante normalizada


def test_endpoint_recusa_nao_imagem(logged_client):
    r = _upload(logged_client, b"texto puro", name="nota.txt")
    assert r.status_code == 400
    r = _upload(logged_client, _png(), name="foto.png")
    assert r.status_code == 201  # controle: png passa


def test_endpoint_recusa_oversize(logged_client):
    r = _upload(logged_client, b"z" * (MAX_IMAGE_BYTES + 1), name="grande.png")
    assert r.status_code == 400
    assert "5 MiB" in r.json()["message"]


def test_endpoint_exige_login(client):
    r = _upload(client, _png())
    assert r.status_code in (302, 403)


def test_envio_com_imagem_persiste_blocos_e_lista_sem_base64(logged_client, conversation):
    up = _upload(logged_client, _png()).json()
    payload = {
        "content": "o que há na imagem?",
        "images": [{"media_type": up["media_type"], "data": up["data"]}],
        "idempotency_key": "img-1",
    }
    r = logged_client.post(
        f"/api/conversations/{conversation.uuid}/messages",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert r.status_code == 201
    from chat.models import Message

    msg = Message.objects.get(uuid=r.json()["user_message_id"])
    assert msg.text == "o que há na imagem?"
    kinds = [b["type"] for b in msg.blocks]
    assert kinds == ["text", "image"]
    assert msg.blocks[1]["source"]["media_type"] == "image/png"

    lst = logged_client.get(f"/api/conversations/{conversation.uuid}/messages").json()
    mine = [m for m in lst["results"] if m["uuid"] == r.json()["user_message_id"]][0]
    assert mine["images"][0]["thumbnail"].startswith("data:image/png;base64,")
    assert "data" not in mine["images"][0]  # listagem leve, sem base64


def test_envio_rejeita_5_imagens_e_mantem_texto_puro(logged_client, conversation):
    up = _upload(logged_client, _png()).json()
    item = {"media_type": up["media_type"], "data": up["data"]}
    bad = {"content": "x", "images": [item] * 5, "idempotency_key": "img-5"}
    r = logged_client.post(
        f"/api/conversations/{conversation.uuid}/messages",
        data=json.dumps(bad),
        content_type="application/json",
    )
    assert r.status_code == 400
    ok = {"content": "só texto", "idempotency_key": "img-t"}
    r = logged_client.post(
        f"/api/conversations/{conversation.uuid}/messages",
        data=json.dumps(ok),
        content_type="application/json",
    )
    assert r.status_code == 201


def _client_ok():
    mgr = FakeStreamManager(FakeStream(["ok"], FakeFinalMessage("pronto")))
    return FakeClient(FakeMessagesNamespace(stream_manager=mgr))


async def test_payload_do_provedor_contem_bloco_image(user, conversation):
    from chat.services.images import validate_image_bytes

    item = await adb(validate_image_bytes)(_png())
    run, _, _ = await adb(reserve_run)(
        conversation=conversation, content="descreva", idempotency_key="k-img", images=[item]
    )
    client = _client_ok()
    events = [e async for e in execute_run(str(run.uuid), user=user, client=client)]
    assert events[-1]["type"] == "done"
    sent = client.messages.calls["stream"][0]["messages"]
    current = sent[-1]
    assert current["role"] == "user"
    assert isinstance(current["content"], list)
    by_type = [b["type"] for b in current["content"]]
    assert by_type == ["text", "image"]
    src = current["content"][1]["source"]
    assert src == {"type": "base64", "media_type": "image/png", "data": item["data"]}


async def test_idempotencia_cobre_imagens(user, conversation):
    from chat.services.images import validate_image_bytes

    item = await adb(validate_image_bytes)(_png())
    run1, created1, _ = await adb(reserve_run)(
        conversation=conversation, content="a", idempotency_key="k", images=[item]
    )
    assert created1 is True
    run2, created2, _ = await adb(reserve_run)(
        conversation=conversation, content="a", idempotency_key="k", images=[item]
    )
    assert (created2, run2.pk) == (False, run1.pk)
