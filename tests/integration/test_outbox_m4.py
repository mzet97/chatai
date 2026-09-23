"""M4: outbox de ingestão — entrega garantida, consumidor idempotente.

Sem rede/broker real: LocalBroker em memória + SQLite. Prova a semântica
do §15 (outbox → dispatcher → consumidor idempotente): falha entre
publish e marcação não perde o job; duplicata não duplica efeitos.
RabbitMQ/Celery/Elastic reais ficam para o homelab (M6 preflight).
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from chat.models_rag import Chunk, IngestionJob, OutboxMessage

pytestmark = pytest.mark.django_db(transaction=True)

CONTENT = "No projeto fictício Aurora, a revisão ocorre às quintas, às 14h.".encode()


@pytest.fixture
def base(logged_client):
    r = logged_client.post(
        "/api/rag/bases", data={"name": "Outbox"}, content_type="application/json"
    )
    assert r.status_code in (200, 201)
    return r.json()["uuid"]


def _upload(client, base_uuid, name="aurora.txt"):
    data = {"file": SimpleUploadedFile(name, CONTENT), "client_key": name}
    r = client.post(f"/api/rag/bases/{base_uuid}/upload", data)
    assert r.status_code == 202
    return r.json()


def test_upload_cria_outbox_pendente(base, logged_client):
    body = _upload(logged_client, base)
    msg = OutboxMessage.objects.get(key=body["job_id"])
    assert msg.topic == "ingest.requested"
    assert msg.state == "pending"
    assert msg.payload["job_uuid"] == body["job_id"]


def test_dispatch_entrega_e_job_fica_ready(base, logged_client):
    from chat.services.ingest.broker import LocalBroker
    from chat.services.ingest.dispatcher import dispatch_pending

    body = _upload(logged_client, base)
    broker = LocalBroker()
    result = dispatch_pending(broker=broker, limit=10)
    assert result == {"delivered": 1, "failed": 0, "remaining": 0}
    assert broker.published and broker.published[0][0] == "ingest.requested"

    msg = OutboxMessage.objects.get(key=body["job_id"])
    assert msg.state == "delivered"
    job = IngestionJob.objects.get(uuid=body["job_id"])
    assert job.state == "ready"
    assert Chunk.objects.filter(version=job.version).exists()


def test_falha_entre_publish_e_marcacao_nao_perde_job(base, logged_client):
    """Broker publica mas cai antes do confirm: redispatch entrega; sem duplicar."""
    from chat.services.ingest.broker import LocalBroker
    from chat.services.ingest.dispatcher import dispatch_pending

    body = _upload(logged_client, base)

    class CrashAfterPublish(LocalBroker):
        def publish(self, topic, payload):
            super().publish(topic, payload)
            raise TimeoutError("queda após publish, antes do confirm")

    result = dispatch_pending(broker=CrashAfterPublish(), limit=10)
    assert result == {"delivered": 0, "failed": 0, "remaining": 1}
    msg = OutboxMessage.objects.get(key=body["job_id"])
    assert msg.state == "pending" and msg.attempts == 1

    result = dispatch_pending(broker=LocalBroker(), limit=10)
    assert result == {"delivered": 1, "failed": 0, "remaining": 0}
    job = IngestionJob.objects.get(uuid=body["job_id"])
    assert job.state == "ready"
    n_chunks = Chunk.objects.filter(version=job.version).count()
    assert n_chunks > 0

    # Entrega duplicada (replay do broker) não duplica efeitos.
    from chat.services.ingest.consumer import handle_ingest_requested

    assert handle_ingest_requested(msg.payload) == "ready"
    assert Chunk.objects.filter(version=job.version).count() == n_chunks


def test_comando_dispatch_outbox_once(base, logged_client):
    from django.core.management import call_command

    _upload(logged_client, base)
    # O comando usa o broker local no perfil local-lite.
    call_command("dispatch_outbox", once=True)
    assert OutboxMessage.objects.filter(state="delivered").count() == 1


def test_indice_local_indexa_e_busca_sem_dependencias():
    import pytest

    from chat.services.ingest.index import ElasticIndex, LocalFtsIndex, select_index

    idx = LocalFtsIndex()
    assert idx.index_chunks([(9001, "aurora quintas revisão")]) == 1
    hits = idx.search("aurora", chunk_ids=[9001], limit=5)
    assert [cid for cid, _ in hits] == [9001]

    with pytest.raises(RuntimeError, match="ELASTIC_URL"):
        ElasticIndex(url=None)
    with pytest.raises(ModuleNotFoundError, match="elasticsearch"):
        select_index("elastic")
