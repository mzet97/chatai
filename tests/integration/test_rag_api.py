"""M1: API de bases/upload — fila, sem "Pronto" antes do worker, isolamento."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

pytestmark = pytest.mark.django_db(transaction=True)


def test_criar_base_e_upload_responde_processing(logged_client):
    r = logged_client.post("/api/rag/bases", data={"name": "Manuais"},
                           content_type="application/json")
    assert r.status_code in (200, 201)
    base_uuid = r.json()["uuid"]
    up = logged_client.post(
        f"/api/rag/bases/{base_uuid}/upload",
        {"file": SimpleUploadedFile("nota.txt", "O prazo é 30 dias.".encode())},
    )
    assert up.status_code == 202
    body = up.json()
    assert body["state"] == "processing" and body["job_id"]
    job = logged_client.get(f"/api/rag/jobs/{body['job_id']}")
    assert job.json()["state"] == "queued"
    docs = logged_client.get(f"/api/rag/bases/{base_uuid}/documents")
    assert docs.json()["results"][0]["state"] == "processing"


def test_upload_invalido_rejeitado(logged_client):
    r = logged_client.post("/api/rag/bases", data={"name": "B"},
                           content_type="application/json")
    base_uuid = r.json()["uuid"]
    bad = logged_client.post(
        f"/api/rag/bases/{base_uuid}/upload",
        {"file": SimpleUploadedFile("x.exe", b"MZ" + b"\x00" * 10)},
    )
    assert bad.status_code == 400


def test_bases_isoladas_por_usuario(logged_client, user2):
    from django.test import Client

    logged_client.post("/api/rag/bases", data={"name": "M"},
                       content_type="application/json")
    c2 = Client()
    c2.force_login(user2)
    assert c2.get("/api/rag/bases").json()["results"] == []
    assert logged_client.get("/api/rag/bases").json()["results"] != []


def test_sources_por_conversa_get_put(logged_client, user):
    from chat.models import Conversation

    conv = Conversation.objects.create(owner=user, title="t")
    r = logged_client.get(f"/api/rag/conversations/{conv.uuid}/sources")
    assert r.json() == {"bases": [], "mode": "always", "coverage": {}}
    b = logged_client.post("/api/rag/bases", data={"name": "B"},
                           content_type="application/json").json()
    r = logged_client.put(
        f"/api/rag/conversations/{conv.uuid}/sources",
        data={"bases": [b["uuid"], "00000000-0000-0000-0000-000000000000"], "mode": "tools"},
        content_type="application/json",
    )
    assert r.status_code == 200
    body = r.json()
    assert body["bases"] == [b["uuid"]] and body["mode"] == "tools"


def test_sources_rejeita_com_run_ativa(logged_client, user):
    from chat.models import Conversation, GenerationRun, Message

    conv = Conversation.objects.create(owner=user, title="t")
    msg = Message.objects.create(conversation=conv, seq=1, role="user", text="oi")
    run = GenerationRun.objects.create(
        conversation=conv, user_message=msg, idempotency_key="k",
        content_hash="x" * 64, state="streaming",
    )
    conv.active_run = run
    conv.save(update_fields=["active_run"])
    r = logged_client.put(
        f"/api/rag/conversations/{conv.uuid}/sources",
        data={"bases": [], "mode": "always"},
        content_type="application/json",
    )
    assert r.status_code == 409
    assert r.json()["code"] == "run_busy"


def test_sources_de_outro_usuario_404(logged_client, user2):
    from chat.models import Conversation

    conv = Conversation.objects.create(owner=user2, title="alheia")
    r = logged_client.get(f"/api/rag/conversations/{conv.uuid}/sources")
    assert r.status_code == 404
