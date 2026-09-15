"""Worker de ingestão (RAG-03). Fila SQLite, lease, idempotência, sem thread de view."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from chat.models_rag import DocumentVersion, IngestionJob

LEASE_MINUTES = 5
WORKER_ID_MAX = 120


def claim_next_job(worker_id: str) -> IngestionJob | None:
    """Aquisição atômica: um UPDATE vencedor (sem select_for_update)."""
    from django.db.models import Q

    now = timezone.now()
    with transaction.atomic():
        updated = (
            IngestionJob.objects.filter(state="queued")
            .filter(Q(claimed_by="") | Q(lease_until__lt=now) | Q(lease_until=None))
            .order_by("created_at")
            .values_list("pk", flat=True)[:1]
        )
        pks = list(updated)
        if not pks:
            # Lease expirado volta para a fila (reinício de worker morto).
            stale = (
                IngestionJob.objects.filter(
                    state__in=("extracting", "chunking", "embedding", "publishing"),
                    lease_until__lt=now,
                )
                .order_by("lease_until")
                .values_list("pk", flat=True)[:1]
            )
            pks = list(stale)
        if not pks:
            return None
        n = IngestionJob.objects.filter(
            pk=pks[0],
            state__in=("queued", "extracting", "chunking", "embedding", "publishing"),
        ).update(
            claimed_by=worker_id[:WORKER_ID_MAX],
            lease_until=now + timedelta(minutes=LEASE_MINUTES),
            updated_at=now,
        )
        if not n:
            return None
        return IngestionJob.objects.get(pk=pks[0])


def _set_state(job: IngestionJob, state: str, **extra) -> None:
    job.state = state
    for key, value in extra.items():
        setattr(job, key, value)
    job.lease_until = timezone.now() + timedelta(minutes=LEASE_MINUTES)
    job.save(update_fields=["state", "lease_until", "updated_at", *extra.keys()])


def _stale(job_uuid, worker_id: str) -> bool:
    """Job cancelado/excluído ou de outra tentativa: não publica."""
    try:
        current = IngestionJob.objects.get(uuid=job_uuid)
    except IngestionJob.DoesNotExist:
        return True
    return current.state in ("cancelled", "failed") or current.claimed_by != worker_id


def process_job(job_uuid, worker_id: str, *, storage_root: Path | None = None,
                file_map: dict | None = None) -> str:
    """Executa as fases M1 (extração). Retorna o estado final."""
    from chat.services.rag.extract import extract_document

    job = IngestionJob.objects.select_related("document", "version").get(uuid=job_uuid)
    version: DocumentVersion = job.version
    # Revalida posse: cancelado/excluído ou de outro worker não executa.
    if job.state not in ("queued", "extracting", "chunking", "embedding", "publishing"):
        return job.state
    if job.claimed_by and job.claimed_by != worker_id:
        return job.state
    try:
        _set_state(job, "extracting", attempt=job.attempt + 1)
        if file_map and str(version.uuid) in file_map:
            data_path = Path(file_map[str(version.uuid)])
            raw = data_path.read_bytes()
        else:
            from chat.services.rag.storage import rag_root

            root = storage_root or rag_root()
            raw = (root / version.rel_path).read_bytes()
        ext = "." + version.filename.rsplit(".", 1)[-1].lower()
        out = extract_document(raw, ext)
        if _stale(job_uuid, worker_id):
            return "cancelled"
        if out.needs_ocr:
            with transaction.atomic():
                version.warnings = out.warnings
                version.extractor = out.extractor
                version.save(update_fields=["warnings", "extractor"])
                job.state = "needs_ocr"
                job.error = "PDF sem texto extraível; OCR fora do escopo."
                job.save(update_fields=["state", "error", "updated_at"])
                doc = job.document
                doc.state = "needs_ocr"
                doc.save(update_fields=["state"])
            return "needs_ocr"
        with transaction.atomic():
            version.text = out.text
            version.locators = out.locators
            version.warnings = out.warnings
            version.extractor = out.extractor
            version.save(update_fields=["text", "locators", "warnings", "extractor"])
            # M1 termina na extração validada; M2 continua chunking→publishing.
            job.state = "ready"
            job.progress_known = 1
            job.progress_total = 1
            job.save(update_fields=["state", "progress_known", "progress_total", "updated_at"])
            doc = job.document
            doc.state = "ready" if not out.warnings else "partial"
            if doc.active_version_id is None:
                doc.active_version = version
                version.is_staging = False
                version.save(update_fields=["is_staging"])
            doc.save(update_fields=["state", "active_version"])
        return "ready"
    except Exception as exc:
        job.state = "failed"
        job.error = f"{type(exc).__name__}: {str(exc)[:200]}"
        job.save(update_fields=["state", "error", "updated_at"])
        job.document.state = "failed"
        job.document.save(update_fields=["state"])
        return "failed"


def _tmp_wrap(raw: bytes, ext: str) -> Path:
    import tempfile

    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    tmp.write(raw)
    tmp.close()
    return Path(tmp.name)


def run_worker_once(worker_id: str, *, storage_root: Path | None = None,
                    file_map: dict | None = None, max_jobs: int = 10) -> int:
    """Reivindica e processa até max_jobs. Retorna quantos processou."""
    done = 0
    for _ in range(max_jobs):
        job = claim_next_job(worker_id)
        if job is None:
            break
        process_job(job.uuid, worker_id, storage_root=storage_root, file_map=file_map)
        done += 1
    return done
