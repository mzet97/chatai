"""Upload integrado: idempotência, conflito de nome, retry, CSRF, isolamento."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

pytestmark = pytest.mark.django_db(transaction=True)

CONTENT = "No projeto fictício Aurora, a revisão interna ocorre às quintas-feiras, às 14h.".encode()
OTHER = "Outro conteúdo para o mesmo nome de arquivo.".encode()


@pytest.fixture
def base(logged_client):
    r = logged_client.post(
        "/api/rag/bases", data={"name": "Upload"}, content_type="application/json")
    assert r.status_code in (200, 201)
    return r.json()["uuid"]


def _post(logged_client, base_uuid, name="aurora.txt", content=CONTENT, **extra):
    data = {"file": SimpleUploadedFile(name, content), **extra}
    return logged_client.post(f"/api/rag/bases/{base_uuid}/upload", data)


def test_idempotente_mesma_chave(base, logged_client):
    from chat.models_rag import Document, IngestionJob

    r1 = _post(logged_client, base, client_key="k-1")
    assert r1.status_code == 202
    r2 = _post(logged_client, base, client_key="k-1")
    assert r2.status_code in (200, 202)
    assert r2.json()["duplicate"] is True
    assert r2.json()["job_id"] == r1.json()["job_id"]
    assert Document.objects.count() == 1
    assert IngestionJob.objects.count() == 1


def test_conteudo_identico_recupera_entrada(base, logged_client):
    from chat.models_rag import Document

    r1 = _post(logged_client, base, client_key="k-a")
    assert r1.status_code == 202
    r2 = _post(logged_client, base, client_key="k-b")  # outra chave, mesmo bytes
    assert r2.json()["duplicate"] is True
    assert r2.json()["doc_uuid"] == r1.json()["doc_uuid"]
    assert Document.objects.count() == 1


def test_nome_igual_conteudo_novo_exige_escolha(base, logged_client):
    from chat.models_rag import Document, DocumentVersion

    assert _post(logged_client, base, content=CONTENT).status_code == 202
    r = _post(logged_client, base, content=OTHER)
    assert r.status_code == 409
    assert r.json()["code"] == "name_conflict"
    rv = _post(logged_client, base, content=OTHER, on_name_conflict="version")
    assert rv.status_code == 202
    assert Document.objects.count() == 1
    assert DocumentVersion.objects.count() == 2
    rs = _post(logged_client, base, content=OTHER + b"x", on_name_conflict="separate")
    assert rs.status_code == 202
    assert Document.objects.count() == 2


def test_uploads_concorrentes_sem_500(base, user):
    """2 POSTs simultâneos (lote da UI): ambos 202, sem database is locked."""
    import threading

    from django.test import Client

    from chat.models_rag import Document

    results = {}

    def up(key, name, content):
        c = Client()
        c.force_login(user)
        r = c.post(
            f"/api/rag/bases/{base}/upload",
            {"file": SimpleUploadedFile(name, content), "client_key": key},
        )
        results[key] = r.status_code

    ts = [
        threading.Thread(target=up, args=("cc-1", "c1.txt", b"conteudo um aqui")),
        threading.Thread(target=up, args=("cc-2", "c2.txt", b"conteudo dois aqui")),
    ]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results == {"cc-1": 202, "cc-2": 202}, results
    assert Document.objects.count() == 2


def test_validacao_e_limites(base, logged_client):
    assert logged_client.post(f"/api/rag/bases/{base}/upload", {}).status_code == 400  # sem file
    r = logged_client.post(
        f"/api/rag/bases/{base}/upload",
        {"file": SimpleUploadedFile("x.exe", b"MZ" + b"\x00" * 10)},
    )
    assert r.status_code == 400
    big = _post(logged_client, base, content=b"y" * (20 * 1024 * 1024 + 1))
    assert big.status_code == 400


def test_isolamento_entre_usuarios(base, logged_client, user2):
    from chat.models_rag import IngestionJob

    r = _post(logged_client, base)
    job_id = r.json()["job_id"]
    c2 = Client()
    c2.force_login(user2)
    other = {"file": SimpleUploadedFile("a.txt", b"oi")}
    assert c2.post(f"/api/rag/bases/{base}/upload", other).status_code == 404
    assert c2.get(f"/api/rag/jobs/{job_id}").status_code == 404
    assert IngestionJob.objects.filter(owner=user2).count() == 0


def test_csrf_bloqueia_sem_token(base, user):
    c = Client(enforce_csrf_checks=True)
    c.force_login(user)
    r = c.post(
        f"/api/rag/bases/{base}/upload",
        {"file": SimpleUploadedFile("a.txt", b"conteudo valido aqui")},
    )
    assert r.status_code == 403


def test_retry_somente_de_estado_final(base, logged_client):
    from chat.models_rag import IngestionJob

    job_id = _post(logged_client, base).json()["job_id"]
    assert logged_client.post(f"/api/rag/jobs/{job_id}/retry").status_code == 409
    job = IngestionJob.objects.get(uuid=job_id)
    job.state = "failed"
    job.error = "boom"
    job.save()
    r = logged_client.post(f"/api/rag/jobs/{job_id}/retry")
    assert r.status_code == 202
    job.refresh_from_db()
    assert (job.state, job.attempt, job.error) == ("queued", 1, "")


def test_worker_status_offline_e_online(logged_client, tmp_path):
    from django.test import override_settings

    from chat.services.rag import heartbeat

    with override_settings(RAG_STORAGE_DIR=str(tmp_path / "rag")):
        assert logged_client.get("/api/rag/worker/status").json()["online"] is False
        heartbeat.beat("teste")
        body = logged_client.get("/api/rag/worker/status").json()
        assert body == {"online": True, "last_seen": body["last_seen"], "worker_id": "teste"}


def test_original_sem_rota_publica(base, logged_client):
    from chat.models_rag import DocumentVersion

    _post(logged_client, base, name="../../trav.txt", content=CONTENT)
    ver = DocumentVersion.objects.get()
    assert ".." not in ver.rel_path
    assert not ver.rel_path.startswith("static")
    anon = Client()
    assert anon.get(f"/api/rag/documents/{ver.document.uuid}/download").status_code in (301, 302)
    assert anon.get("/api/rag/worker/status").status_code in (301, 302)
