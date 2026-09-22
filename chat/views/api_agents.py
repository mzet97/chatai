"""API de agentes: cadastro, versões, exemplos e seleção por conversa."""

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from chat.models import Conversation
from chat.models_agents import AgentDefinition, AgentVersion
from chat.services import variation as _variation
from chat.services.agents import cache as _cache
from chat.services.agents import profiles
from chat.services.agents import thinking as _agent_thinking

VERSION_FIELDS = (
    "task_instructions",
    "model",
    "allowed_tools",
    "allowed_kb_ids",
    "delegatable_ids",
    "thinking_mode",
    "thinking_level",
    "thinking_budget",
    "variation_level",
    "cache_mode",
    "cache_ttl",
    "max_child_runs",
)


def _version_json(v: AgentVersion) -> dict:
    return {
        "uuid": str(v.uuid),
        "revision": v.revision,
        "published": v.published,
        "complete": profiles.is_complete(v),
        **{f: getattr(v, f) for f in VERSION_FIELDS},
    }


def _definition_json(d: AgentDefinition) -> dict:
    versions = [_version_json(v) for v in d.versions.all()[:10]]
    return {
        "uuid": str(d.uuid),
        "name": d.name,
        "description": d.description,
        "kind": d.kind,
        "archived": d.archived,
        "versions": versions,
    }


def _owned_definition(user, agent_uuid) -> AgentDefinition:
    try:
        return AgentDefinition.objects.get(uuid=agent_uuid, owner=user)
    except AgentDefinition.DoesNotExist as exc:
        raise LookupError("agente") from exc


def _body(request) -> dict:
    try:
        body = json.loads(request.body or "{}")
    except (ValueError, TypeError, UnicodeDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


@login_required
@require_http_methods(["GET", "POST"])
def agents(request):
    if request.method == "POST":
        body = _body(request)
        name = (body.get("name") or "").strip()
        if not name:
            return JsonResponse({"code": "validation", "message": "Nome obrigatório."}, status=400)
        if AgentDefinition.objects.filter(owner=request.user, name=name).exists():
            return JsonResponse(
                {"code": "validation", "message": "Já existe um agente com esse nome."},
                status=400,
            )
        definition = AgentDefinition.objects.create(
            owner=request.user,
            name=name[:120],
            description=(body.get("description") or "")[:2000],
            kind=(body.get("kind") or "general")[:20],
        )
        AgentVersion.objects.create(definition=definition, revision=1)
        return JsonResponse(_definition_json(definition), status=201)
    qs = AgentDefinition.objects.filter(owner=request.user)
    if (request.GET.get("archived") or "") != "1":
        qs = qs.filter(archived=False)
    return JsonResponse({"results": [_definition_json(d) for d in qs.order_by("name")]})


@login_required
@require_http_methods(["GET", "PATCH", "DELETE"])
def agent_detail(request, agent_uuid):
    try:
        definition = _owned_definition(request.user, agent_uuid)
    except LookupError:
        return JsonResponse({"code": "not_found", "message": "Agente não encontrado."}, status=404)
    if request.method == "GET":
        return JsonResponse(_definition_json(definition))
    if request.method == "DELETE":
        definition.delete()
        return JsonResponse({"deleted": True})
    body = _body(request)
    if "name" in body:
        name = (body["name"] or "").strip()
        if not name:
            return JsonResponse({"code": "validation", "message": "Nome obrigatório."}, status=400)
        definition.name = name[:120]
    if "description" in body:
        definition.description = (body["description"] or "")[:2000]
    if "archived" in body:
        definition.archived = bool(body["archived"])
    definition.save()
    return JsonResponse(_definition_json(definition))


@login_required
@require_http_methods(["POST"])
def agent_duplicate(request, agent_uuid):
    try:
        src = _owned_definition(request.user, agent_uuid)
    except LookupError:
        return JsonResponse({"code": "not_found", "message": "Agente não encontrado."}, status=404)
    base = f"{src.name} (cópia)"
    name, i = base[:120], 2
    while AgentDefinition.objects.filter(owner=request.user, name=name).exists():
        name = f"{base} {i}"[:120]
        i += 1
    copy = AgentDefinition.objects.create(
        owner=request.user, name=name, description=src.description, kind=src.kind
    )
    last = src.versions.order_by("-revision").first()
    data = {f: getattr(last, f) for f in VERSION_FIELDS} if last else {}
    AgentVersion.objects.create(definition=copy, revision=1, **data)
    return JsonResponse(_definition_json(copy), status=201)


@login_required
@require_http_methods(["POST"])
def agent_examples(request):
    out = profiles.ensure_examples(request.user)
    return JsonResponse({**out, "total": 4})


@login_required
@require_http_methods(["PATCH"])
def version_detail(request, version_uuid):
    try:
        version = AgentVersion.objects.select_related("definition").get(
            uuid=version_uuid, definition__owner=request.user
        )
    except AgentVersion.DoesNotExist:
        return JsonResponse({"code": "not_found", "message": "Versão não encontrada."}, status=404)
    if version.published:
        return JsonResponse(
            {"code": "immutable", "message": "Versão publicada é imutável; crie um rascunho."},
            status=409,
        )
    body = _body(request)
    # Pensamento por agente (M3/AG-4.1): enum fora do contrato → 400, sem
    # salvar (dado inválido nunca chega ao runtime nem ao snapshot).
    errors = _agent_thinking.validate_fields(body)
    if "variation_level" in body:
        try:
            _variation.normalize_level(body["variation_level"])
        except Exception as exc:
            errors["variation_level"] = str(exc) or "Nível de variação inválido."
    for key, valid in (("cache_mode", _cache.MODES), ("cache_ttl", _cache.TTLS)):
        if key in body:
            raw = (body[key] or "").strip().lower() if isinstance(body[key], str) else ""
            if raw not in valid:
                errors[key] = f"{key}: {', '.join(valid)}."
    if errors:
        return JsonResponse(
            {"code": "validation", "message": "Campos de versão inválidos.", "fields": errors},
            status=400,
        )
    for field in VERSION_FIELDS:
        if field in body:
            setattr(version, field, body[field])
    version.save()
    return JsonResponse(_version_json(version))


@login_required
@require_http_methods(["POST"])
def version_publish(request, version_uuid):
    try:
        version = AgentVersion.objects.select_related("definition").get(
            uuid=version_uuid, definition__owner=request.user
        )
    except AgentVersion.DoesNotExist:
        return JsonResponse({"code": "not_found", "message": "Versão não encontrada."}, status=404)
    try:
        profiles.publish_version(version.pk, request.user)
    except ValueError as exc:
        return JsonResponse({"code": "validation", "message": str(exc)}, status=400)
    version.refresh_from_db()
    return JsonResponse(_version_json(version))


@login_required
@require_http_methods(["POST"])
def agent_new_draft(request, agent_uuid):
    try:
        definition = _owned_definition(request.user, agent_uuid)
    except LookupError:
        return JsonResponse({"code": "not_found", "message": "Agente não encontrado."}, status=404)
    draft = profiles.new_draft(definition.pk, request.user)
    return JsonResponse(_version_json(draft), status=201)


def resolve_selection(user, body: dict):
    """Valida modo + perfil; retorna (mode, definition) ou erro JsonResponse."""
    mode = (body.get("agent_mode") or "chat").strip().lower()
    if mode not in ("chat", "agent", "team"):
        return None, JsonResponse(
            {"code": "validation", "message": "Modo desconhecido: chat, agent ou team."},
            status=400,
        )
    definition = None
    ref = body.get("agent_definition_uuid")
    if ref:
        try:
            definition = AgentDefinition.objects.get(uuid=ref, owner=user, archived=False)
        except AgentDefinition.DoesNotExist:
            return None, JsonResponse(
                {"code": "validation", "message": "Agente inválido ou arquivado."},
                status=400,
            )
    if mode == "team" and definition is not None and definition.kind != "coordinator":
        return None, JsonResponse(
            {"code": "validation", "message": "Equipe exige perfil coordenador."},
            status=400,
        )
    if mode in ("agent", "team") and definition is None:
        return None, JsonResponse(
            {"code": "validation", "message": "Escolha um perfil para este modo."},
            status=400,
        )
    return (mode, definition), None


def apply_selection(conversation: Conversation, selection) -> None:
    mode, definition = selection
    conversation.agent_mode = mode
    conversation.agent_definition = definition
