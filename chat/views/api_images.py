"""Anexo de imagens do compositor (M3; segurança M5).

POST /api/images (multipart, campo `file`): valida com Pillow, normaliza e
devolve o payload canônico + miniatura. Stateless e separado do Documento
RAG (que indexa arquivos em bases); aqui nada é persistido — o POST de
envio referencia os payloads e o servidor revalida antes de guardar.

M5: o nome do arquivo é dado não confiável — passa por DLP
(`check_filename_safety`: segredo aparente recusa sem eco; resto
sanitizado em `name`) e nunca vira autorização nem HTML.
"""

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from chat.services.images import MAX_IMAGE_BYTES, check_filename_safety, validate_image_bytes


@login_required
@require_http_methods(["POST"])
def upload_image(request):
    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse({"code": "validation", "message": "Arquivo ausente."}, status=400)
    try:
        name = check_filename_safety(upload.name)
    except ValueError as exc:
        return JsonResponse({"code": "validation", "message": str(exc)}, status=400)
    content = upload.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        return JsonResponse({"code": "validation", "message": "Imagem excede 5 MiB."}, status=400)
    try:
        item = validate_image_bytes(content)
    except ValueError as exc:
        return JsonResponse({"code": "validation", "message": str(exc)}, status=400)
    item["name"] = name
    return JsonResponse(item, status=201)
