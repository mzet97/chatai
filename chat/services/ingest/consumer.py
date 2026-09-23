"""M4: consumidor idempotente de `ingest.requested` (§15).

Revalida job/versão antes de agir; estados terminais são no-op, então
duplicatas do broker não duplicam efeitos. O job continua na fila do
`rag_worker` como fallback: confirmar entrega ao broker não comprova
processamento.
"""

from __future__ import annotations

from pathlib import Path

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from chat.models_rag import IngestionJob
from chat.services.rag.worker import LEASE_MINUTES, WORKER_ID_MAX, process_job

TERMINAL = frozenset({"ready", "cancelled", "failed", "needs_ocr"})
ACTIVE = ("queued", "extracting", "chunking", "embedding", "publishing")


def enqueue_ingest(job: IngestionJob) -> None:
    """Grava a mensagem outbox na mesma transação do chamador.

    Chamar dentro do `transaction.atomic()` que cria o job: upload e
    outbox nascem juntos; sem transação única com S3/Elastic (§14).
    """
    from chat.models_rag import OutboxMessage

    OutboxMessage.objects.get_or_create(
        key=str(job.uuid),
        defaults={
            "topic": "ingest.requested",
            "payload": {
                "job_uuid": str(job.uuid),
                "version_uuid": str(job.version.uuid),
            },
        },
    )


def handle_ingest_requested(
    payload: dict,
    *,
    file_map: dict | None = None,
    storage_root: Path | None = None,
) -> str:
    """Consome uma entrega; retorna o estado final do job."""
    try:
        job = IngestionJob.objects.select_related("document", "document__base", "version").get(
            uuid=payload["job_uuid"]
        )
    except (IngestionJob.DoesNotExist, KeyError):
        return "stale"
    if job.state in TERMINAL:
        return job.state
    if str(job.version.uuid) != payload.get("version_uuid"):
        return "stale"  # versão substituída: não ressuscita publicação antiga
    worker_id = f"ingest-{job.uuid}"[:WORKER_ID_MAX]
    now = timezone.now()
    with transaction.atomic():
        claimed = (
            IngestionJob.objects.filter(pk=job.pk, state__in=ACTIVE)
            .filter(Q(claimed_by="") | Q(lease_until__lt=now) | Q(lease_until=None))
            .update(
                claimed_by=worker_id,
                lease_until=now + timezone.timedelta(minutes=LEASE_MINUTES),
                updated_at=now,
            )
        )
    if not claimed:
        job.refresh_from_db()
        return job.state
    return process_job(job.uuid, worker_id, storage_root=storage_root, file_map=file_map)
