"""M5: página Conhecimento, APIs de gestão, download, CSRF, isolamento."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def base_id(logged_client):
    r = logged_client.post("/api/rag/bases", data={"name": "UI"},
                           content_type="application/json")
    return r.json()["uuid"]


def test_knowledge_page_exige_login_e_tem_upload(client):
    r = client.get("/knowledge/")
    assert r.status_code == 302  # login
    assert "/login" in r["Location"]


def test_knowledge_page_tem_envio_e_dialogos(logged_client):
    r = logged_client.get("/knowledge/")
    assert r.status_code == 200
    html = r.content.decode()
    # Entrada única "Enviar documentos" (diálogo compartilhado), visível no vazio.
    assert 'id="kb-upload-open"' in html and "Enviar documentos" in html
    assert 'id="kb-confirm"' in html and 'id="source-dialog"' not in html
    assert "csrf" in html.lower() or "csrftoken" in html.lower()


def test_index_tem_fontes_e_dialogo_fonte(logged_client):
    r = logged_client.get("/")
    html = r.content.decode()
    assert 'id="sources-btn"' in html and 'id="sources-pop"' in html
    assert 'id="source-dialog"' in html and 'role="dialog"' in html


def test_upload_sem_csrf_bloqueado(user, base_id):
    c = Client(enforce_csrf_checks=True)
    c.force_login(user)
    base_uuid = base_id
    r = c.post(f"/api/rag/bases/{base_uuid}/upload",
               {"file": SimpleUploadedFile("a.txt", b"oi")})
    assert r.status_code == 403


def test_renomear_e_excluir_base_com_confirmacao(logged_client, base_id):
    r = logged_client.put(f"/api/rag/bases/{base_id}/manage",
                          data={"name": "Nova"}, content_type="application/json")
    assert r.json()["name"] == "Nova"
    r = logged_client.delete(f"/api/rag/bases/{base_id}/manage",
                             data={}, content_type="application/json")
    assert r.status_code == 400  # sem confirm
    r = logged_client.delete(f"/api/rag/bases/{base_id}/manage",
                             data={"confirm": True}, content_type="application/json")
    assert r.json()["base"] == "Nova"
    assert logged_client.get("/api/rag/bases").json()["results"] == []


def test_doc_detalhe_download_e_exclusao(logged_client, base_id, tmp_path):
    up = logged_client.post(
        f"/api/rag/bases/{base_id}/upload",
        {"file": SimpleUploadedFile("n.txt", "O prazo é 30 dias.".encode())},
    )
    doc_uuid = up.json()["doc_uuid"]
    d = logged_client.get(f"/api/rag/documents/{doc_uuid}").json()
    assert d["name"] == "n.txt" and "versions" in d and "jobs" in d
    # Sem versão publicada: download indisponível (sem vazar nada).
    dl = logged_client.get(f"/api/rag/documents/{doc_uuid}/download")
    assert dl.status_code == 404
    r = logged_client.delete(f"/api/rag/documents/{doc_uuid}/delete",
                             data={"confirm": True}, content_type="application/json")
    assert r.json()["document"] == "n.txt"
    assert logged_client.get(f"/api/rag/documents/{doc_uuid}").status_code == 404


def test_citations_isoladas_por_conversa(logged_client, user, user2):
    from chat.models import Conversation

    conv = Conversation.objects.create(owner=user, title="t")
    got = logged_client.get(f"/api/rag/conversations/{conv.uuid}/citations").json()
    assert got == {"citations": {}}
    conv2 = Conversation.objects.create(owner=user2, title="x")
    logged_client.force_login(user2)
    got2 = logged_client.get(f"/api/rag/conversations/{conv2.uuid}/citations").json()
    assert got2 == {"citations": {}}
    # Conversa alheia: 404, sem revelar existência de citações.
    r = logged_client.get(f"/api/rag/conversations/{conv.uuid}/citations")
    assert r.status_code == 404


def test_erro_de_job_nao_vaza_conteudo(logged_client, base_id):
    up = logged_client.post(
        f"/api/rag/bases/{base_id}/upload",
        {"file": SimpleUploadedFile("x.txt", b"SEGREDO-123")},
    )
    job_id = up.json()["job_id"]
    job = logged_client.get(f"/api/rag/jobs/{job_id}").json()
    assert "SEGREDO-123" not in job.get("error", "")
