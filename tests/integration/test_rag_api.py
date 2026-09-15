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
