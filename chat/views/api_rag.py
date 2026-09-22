"""API Conhecimento (M1: bases + upload; M4: seleção por conversa)."""

import hashlib

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from chat.models import TERMINAL_RUN_STATES, Conversation
from chat.models_rag import (
    ConversationKnowledge,
    Document,
    DocumentVersion,
    IngestionJob,
    KnowledgeBase,
)
from chat.services.rag.storage import rag_object_store, version_path
from chat.services.rag.validate import MAX_FILE_BYTES, validate_upload


def _owned_base(user, base_uuid):
    return get_object_or_404(KnowledgeBase, uuid=base_uuid, owner=user)


def _accepted(doc, version, job, duplicate=False):
    """Confirmação de recebimento: identidades + estado real, sem prometer índice."""
    return {
        "doc_uuid": str(doc.uuid),
        "version": version.number,
        "job_id": str(job.uuid),
        "state": "processing",
        "duplicate": duplicate,
    }


def _is_busy(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _busy_retry(fn, *, attempts=5):
    """Repete transação curta em contenção SQLite (uploads concorrentes)."""
    import time

    from django.db import OperationalError

    delay = 0.2
    for i in range(attempts):
        try:
            return fn()
        except OperationalError as exc:
            if not _is_busy(exc) or i == attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2


@login_required
@require_http_methods(["GET", "POST"])
def bases(request):
    if request.method == "GET":
        items = KnowledgeBase.objects.filter(owner=request.user).order_by("name")
        return JsonResponse({"results": [{"uuid": str(b.uuid), "name": b.name} for b in items]})
    from chat.views._body import parse_body

    body, err = parse_body(request)
    if err is not None:
        return err
    name = (body.get("name") or "").strip()
    if not name or len(name) > 120:
        return JsonResponse({"code": "validation", "message": "Nome inválido."}, status=400)
    base, created = KnowledgeBase.objects.get_or_create(owner=request.user, name=name)
    return JsonResponse({"uuid": str(base.uuid), "name": base.name}, status=201 if created else 200)


@login_required
@require_http_methods(["GET"])
def base_documents(request, base_uuid):
    base = _owned_base(request.user, base_uuid)
    docs = (
        Document.objects.filter(base=base)
        .select_related("active_version")
        .order_by("-created_at")[:100]
    )
    return JsonResponse(
        {
            "results": [
                {
                    "uuid": str(d.uuid),
                    "name": d.name,
                    "state": d.state,
                    "version": d.active_version.number if d.active_version else None,
                    "size_bytes": d.active_version.size_bytes if d.active_version else None,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
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
        return JsonResponse({"code": "validation", "message": "Arquivo excede 20 MiB."}, status=400)
    try:
        ext = validate_upload(upload.name, content, len(content))
    except ValueError as exc:
        return JsonResponse({"code": "validation", "message": str(exc)}, status=400)
    digest = hashlib.sha256(content).hexdigest()
    client_key = (request.POST.get("client_key") or "")[:64]
    on_conflict = request.POST.get("on_name_conflict") or ""
    written: list = []  # arquivos de tentativas com transação revertida

    def attempt():
        from django.db import OperationalError

        try:
            return _attempt_body()
        except OperationalError:
            from chat.services.rag.storage import rag_object_store as _store_fn

            _store = _store_fn()
            for key in written:  # reverte órfãos da tentativa
                try:
                    _store.delete(key)
                except (OSError, ValueError):
                    pass
            written.clear()
            raise

    def _attempt_body():
        with transaction.atomic():
            if client_key:
                dup = (
                    IngestionJob.objects.select_related("document", "version")
                    .filter(owner=request.user, client_key=client_key)
                    .first()
                )
                if dup is not None:  # reenvio da mesma operação: sem duplicar
                    return JsonResponse(_accepted(dup.document, dup.version, dup, duplicate=True))
            same_content = (
                DocumentVersion.objects.select_related("document")
                .filter(
                    document__base=base,
                    document__owner=request.user,
                    sha256=digest,
                    filename=upload.name,
                )
                .first()
            )
            if same_content is not None:  # conteúdo idêntico: recupera a entrada
                job = (
                    IngestionJob.objects.filter(owner=request.user, version=same_content)
                    .order_by("-id")
                    .first()
                )
                if job is None:
                    job = IngestionJob.objects.create(
                        owner=request.user,
                        document=same_content.document,
                        version=same_content,
                        client_key=client_key,
                    )
                    from chat.services.ingest.consumer import enqueue_ingest

                    enqueue_ingest(job)  # M4: mesma transação do job
                return JsonResponse(
                    _accepted(same_content.document, same_content, job, duplicate=True)
                )
            same_name = Document.objects.filter(
                base=base, owner=request.user, name=upload.name
            ).first()
            if same_name is not None and on_conflict not in ("version", "separate"):
                other = (
                    DocumentVersion.objects.filter(document=same_name)
                    .exclude(sha256=digest)
                    .first()
                )
                if other is not None:  # nome igual, conteúdo novo: escolha explícita
                    return JsonResponse(
                        {
                            "code": "name_conflict",
                            "message": f'"{upload.name}" já existe com outro conteúdo.',
                            "doc_uuid": str(same_name.uuid),
                        },
                        status=409,
                    )
            if same_name is not None and on_conflict == "version":
                doc = same_name
            else:
                doc = Document.objects.create(
                    base=base, owner=request.user, name=upload.name, state="processing"
                )
            number = (DocumentVersion.objects.filter(document=doc).count() or 0) + 1
            version = DocumentVersion.objects.create(
                document=doc,
                number=number,
                sha256=digest,
                filename=upload.name,
                size_bytes=len(content),
                rel_path=f"{base.uuid}/{doc.uuid}/{number}{ext}",
            )
            from chat.services.rag.storage import rag_root

            dest = version_path(request.user.pk, base.uuid, doc.uuid, version.uuid, upload.name)
            store = rag_object_store()
            key = dest.relative_to(rag_root()).as_posix()
            meta = store.put(key, content)
            if meta["sha256"] != digest:  # imutável: o validado é o gravado
                raise ValueError("Divergência entre conteúdo validado e gravado.")
            written.append(key)
            version.rel_path = str(dest.relative_to(rag_root()))
            version.save(update_fields=["rel_path"])
            job = IngestionJob.objects.create(
                owner=request.user, document=doc, version=version, client_key=client_key
            )
            from chat.services.ingest.consumer import enqueue_ingest

            enqueue_ingest(job)  # M4: mesma transação do job
            return ("created", doc, version, job)

    out = _busy_retry(attempt)
    if isinstance(out, JsonResponse):
        return out
    _, doc, version, job = out
    return JsonResponse(_accepted(doc, version, job), status=202)


@login_required
@require_http_methods(["GET"])
def job_status(request, job_uuid):
    job = get_object_or_404(IngestionJob, uuid=job_uuid, owner=request.user)
    return JsonResponse(
        {
            "uuid": str(job.uuid),
            "state": job.state,
            "error": job.error,
            "progress_known": job.progress_known,
            "progress_total": job.progress_total,
        }
    )


@login_required
@require_http_methods(["POST"])
def job_retry(request, job_uuid):
    """Tentar novamente: só de estado final com falha; nova tentativa com geração+1."""

    def attempt():
        with transaction.atomic():
            job = get_object_or_404(IngestionJob, uuid=job_uuid, owner=request.user)
            if job.state not in ("failed", "needs_ocr", "cancelled"):
                return JsonResponse(
                    {
                        "code": "retry_invalid",
                        "message": f"Job em {job.state} não precisa de nova tentativa.",
                    },
                    status=409,
                )
            job.state = "queued"
            job.attempt = (job.attempt or 0) + 1
            job.generation = (job.generation or 0) + 1  # invalida publicação tardia
            job.claimed_by = ""
            job.lease_until = None
            job.error = ""
            job.checkpoint = {}
            job.save(
                update_fields=[
                    "state",
                    "attempt",
                    "generation",
                    "claimed_by",
                    "lease_until",
                    "error",
                    "checkpoint",
                    "updated_at",
                ]
            )
            return ("retried", str(job.uuid), job.state, job.attempt)

    out = _busy_retry(attempt)
    if isinstance(out, JsonResponse):
        return out
    _, uuid, state, attempt_no = out
    return JsonResponse({"uuid": uuid, "state": state, "attempt": attempt_no}, status=202)


@login_required
@require_http_methods(["GET"])
def worker_status(request):
    from chat.services.rag import heartbeat

    return JsonResponse(heartbeat.status())


@login_required
@require_http_methods(["PUT", "DELETE"])
def base_detail(request, base_uuid):
    """M5: renomear (PUT) ou excluir com purga (DELETE + confirmação)."""
    base = _owned_base(request.user, base_uuid)
    from chat.views._body import parse_body

    if request.method == "PUT":
        body, err = parse_body(request)
        if err is not None:
            return err
        name = (body.get("name") or "").strip()
        if not name or len(name) > 120:
            return JsonResponse({"code": "validation", "message": "Nome inválido."}, status=400)
        base.name = name
        base.save(update_fields=["name"])
        return JsonResponse({"uuid": str(base.uuid), "name": base.name})
    body, err = parse_body(request)
    if err is not None:
        return err
    if body.get("confirm") is not True:
        return JsonResponse(
            {"code": "confirm", "message": "Exclusão exige confirm:true."}, status=400
        )
    from chat.services.rag.retention import purge_base

    return JsonResponse(purge_base(base))


@login_required
@require_http_methods(["GET"])
def document_detail(request, doc_uuid):
    """M5: versões, jobs e avisos de cobertura de um documento."""
    from chat.models_rag import DocumentVersion

    doc = get_object_or_404(Document, uuid=doc_uuid, owner=request.user)
    versions = DocumentVersion.objects.filter(document=doc).order_by("-number")
    jobs = IngestionJob.objects.filter(document=doc).order_by("-created_at")[:5]
    return JsonResponse(
        {
            "uuid": str(doc.uuid),
            "name": doc.name,
            "state": doc.state,
            "active_version": doc.active_version.number if doc.active_version else None,
            "versions": [
                {
                    "number": v.number,
                    "state": ("active" if doc.active_version_id == v.pk else "history"),
                    "warnings": v.warnings,
                    "extractor": v.extractor,
                    "chunks": v.chunks.count(),
                }
                for v in versions
            ],
            "jobs": [{"uuid": str(j.uuid), "state": j.state, "error": j.error} for j in jobs],
        }
    )


@login_required
@require_http_methods(["DELETE"])
def document_delete(request, doc_uuid):
    """M5: exclui documento com purga (confirmação explícita)."""
    from chat.views._body import parse_body

    body, err = parse_body(request)
    if err is not None:
        return err
    if body.get("confirm") is not True:
        return JsonResponse(
            {"code": "confirm", "message": "Exclusão exige confirm:true."}, status=400
        )
    doc = get_object_or_404(Document, uuid=doc_uuid, owner=request.user)
    from chat.services.rag.retention import purge_document

    return JsonResponse(purge_document(doc))


@login_required
@require_http_methods(["GET"])
def document_download(request, doc_uuid):
    """M5: download do original da versão ativa (outra ação autorizada)."""
    from django.http import FileResponse

    doc = get_object_or_404(Document, uuid=doc_uuid, owner=request.user)
    ver = doc.active_version
    if ver is None or not ver.rel_path:
        return JsonResponse({"code": "unavailable", "message": "Sem versão publicada."}, status=404)
    from chat.services.rag.storage import rag_root

    path = (rag_root() / ver.rel_path).resolve()
    if rag_root().resolve() not in path.parents or not path.is_file():
        return JsonResponse({"code": "unavailable", "message": "Arquivo indisponível."}, status=404)
    return FileResponse(open(path, "rb"), as_attachment=True, filename=ver.filename)


@login_required
@require_http_methods(["GET"])
def conversation_citations(request, conv_uuid):
    """M5: citações verificadas por mensagem da conversa (painel de fonte)."""
    from chat.models import GenerationRun, Message
    from chat.models_rag import Citation

    conv = get_object_or_404(Conversation, uuid=conv_uuid, owner=request.user)
    # retrieval_run_id → assistant message uuid, via snapshot da geração.
    msg_of_run: dict[int, str] = {}
    for run in GenerationRun.objects.filter(conversation=conv).only(
        "snapshot", "assistant_message"
    ):
        rid = (run.snapshot or {}).get("rag", {}).get("retrieval_run_id")
        if rid and run.assistant_message_id:
            msg = Message.objects.filter(pk=run.assistant_message_id).first()
            if msg is not None:
                msg_of_run[int(rid)] = str(msg.uuid)
    cites = (
        Citation.objects.filter(run__owner=request.user, run_id__in=list(msg_of_run))
        .select_related(
            "chunk",
            "chunk__version",
            "chunk__version__document",
            "chunk__version__document__base",
        )
        .order_by("run_id", "source_index")
    )
    out: dict[str, list] = {}
    for c in cites:
        if c.chunk_id is None:
            # M6: chunk purgado — tombstone indisponível, sem ressuscitar.
            out.setdefault(msg_of_run[c.run_id], []).append(
                {
                    "index": c.source_index,
                    "kb_id": c.kb_id,
                    "locator": c.locator_snapshot,
                    "available": False,
                }
            )
            continue
        doc = c.chunk.version.document
        out.setdefault(msg_of_run[c.run_id], []).append(
            {
                "index": c.source_index,
                "kb_id": c.kb_id,
                "doc": doc.name,
                "base": doc.base.name,
                "version": c.chunk.version.number,
                "locator": c.locator_snapshot,
                "excerpt": c.chunk.text[:500],
                "available": True,
            }
        )
    return JsonResponse({"citations": out})


@login_required
@require_http_methods(["GET", "PUT"])
def conversation_sources(request, conv_uuid):
    """Fontes por conversa (M4): bases autorizadas + modo. Vale p/ próxima run."""
    conv = get_object_or_404(Conversation, uuid=conv_uuid, owner=request.user)
    if request.method == "GET":
        try:
            sel = ConversationKnowledge.objects.get(conversation=conv)
            current = {"bases": sel.bases, "mode": sel.mode}
        except ConversationKnowledge.DoesNotExist:
            current = {"bases": [], "mode": "always"}
        # Cobertura real por base: prontos × em processamento (pendentes não
        # contam como fontes examinadas).
        from django.db.models import Count, Q

        coverage = {}
        for row in (
            Document.objects.filter(base__owner=request.user)
            .values("base__uuid")
            .annotate(
                ready=Count("id", filter=Q(state="ready")),
                pending=Count("id", filter=Q(state__in=("processing", "upload"))),
            )
        ):
            coverage[str(row["base__uuid"])] = {"ready": row["ready"], "processing": row["pending"]}
        current["coverage"] = coverage
        return JsonResponse(current)
    # PUT: rejeita com geração/aprovação pendente (snapshot em andamento).
    if conv.active_run_id is not None:
        state = conv.active_run.state
        if state not in TERMINAL_RUN_STATES:
            return JsonResponse(
                {
                    "code": "run_busy",
                    "message": "Conversa com execução em andamento; tente após concluir.",
                },
                status=409,
            )
    from chat.views._body import parse_body

    body, err = parse_body(request)
    if err is not None:
        return err
    bases = body.get("bases", [])
    mode = body.get("mode", "always")
    if not isinstance(bases, list) or mode not in ConversationKnowledge.MODES:
        return JsonResponse({"code": "validation", "message": "bases/mode inválidos."}, status=400)
    owned = set(KnowledgeBase.objects.filter(owner=request.user).values_list("uuid", flat=True))
    clean = []
    for u in bases:
        try:
            from uuid import UUID

            norm = str(UUID(str(u)))
        except ValueError:
            continue
        if norm in {str(o) for o in owned}:
            clean.append(norm)
    sel, _ = ConversationKnowledge.objects.update_or_create(
        conversation=conv, defaults={"bases": clean, "mode": mode}
    )
    return JsonResponse({"bases": sel.bases, "mode": sel.mode})
