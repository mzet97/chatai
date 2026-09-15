"""API Conhecimento M1: bases + upload com fila (sem "Pronto" antes do worker)."""

import hashlib
import json

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from chat.models_rag import Document, DocumentVersion, IngestionJob, KnowledgeBase
from chat.services.rag.storage import version_path, write_original
from chat.services.rag.validate import MAX_FILE_BYTES, validate_upload


def _owned_base(user, base_uuid):
    return get_object_or_404(KnowledgeBase, uuid=base_uuid, owner=user)


@login_required
@require_http_methods(["GET", "POST"])
def bases(request):
    if request.method == "GET":
        items = KnowledgeBase.objects.filter(owner=request.user).order_by("name")
        return JsonResponse(
            {"results": [{"uuid": str(b.uuid), "name": b.name} for b in items]}
        )
    try:
        body = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"code": "validation", "message": "JSON inválido."}, status=400)
    name = (body.get("name") or "").strip()
    if not name or len(name) > 120:
        return JsonResponse({"code": "validation", "message": "Nome inválido."}, status=400)
    base, created = KnowledgeBase.objects.get_or_create(owner=request.user, name=name)
    return JsonResponse(
        {"uuid": str(base.uuid), "name": base.name}, status=201 if created else 200
    )


@login_required
@require_http_methods(["GET"])
def base_documents(request, base_uuid):
    base = _owned_base(request.user, base_uuid)
    docs = Document.objects.filter(base=base).order_by("-created_at")[:100]
    return JsonResponse(
        {
            "results": [
                {
                    "uuid": str(d.uuid),
                    "name": d.name,
                    "state": d.state,
                    "version": d.active_version.number if d.active_version else None,
                }
                for d in docs
            ]
        }
    )


@login_required
@require_http_methods(["POST"])
def upload_document(request, base_uuid):
    """Multipart {file}. Valida, armazena privado, enfileira. Responde processing."""
    base = _owned_base(request.user, base_uuid)
    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse({"code": "validation", "message": "Arquivo ausente."}, status=400)
    content = upload.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        return JsonResponse(
            {"code": "validation", "message": "Arquivo excede 20 MiB."}, status=400
        )
    try:
        ext = validate_upload(upload.name, content, len(content))
    except ValueError as exc:
        return JsonResponse({"code": "validation", "message": str(exc)}, status=400)
    digest = hashlib.sha256(content).hexdigest()
    with transaction.atomic():
        doc = Document.objects.create(base=base, owner=request.user,
                                      name=upload.name, state="processing")
        existing = DocumentVersion.objects.filter(document=doc, sha256=digest).first()
        number = (DocumentVersion.objects.filter(document=doc).count() or 0) + 1
        version = DocumentVersion.objects.create(
            document=doc, number=number, sha256=digest, filename=upload.name,
            size_bytes=len(content),
            rel_path=f"{base.uuid}/{doc.uuid}/{number}{ext}",
        )
        from chat.services.rag.storage import rag_root

        dest = version_path(request.user.pk, base.uuid, doc.uuid, version.uuid, upload.name)
        write_original(dest, content)
        version.rel_path = str(dest.relative_to(rag_root()))
        version.save(update_fields=["rel_path"])
        job = IngestionJob.objects.create(owner=request.user, document=doc, version=version)
        _ = existing  # duplicata de conteúdo: nova versão explícita, sem reindexar igual
    return JsonResponse(
        {"doc_uuid": str(doc.uuid), "version": number, "job_id": str(job.uuid),
         "state": "processing"},
        status=202,
    )


@login_required
@require_http_methods(["GET"])
def job_status(request, job_uuid):
    job = get_object_or_404(IngestionJob, uuid=job_uuid, owner=request.user)
    return JsonResponse(
        {"uuid": str(job.uuid), "state": job.state, "error": job.error,
         "progress_known": job.progress_known, "progress_total": job.progress_total}
    )
