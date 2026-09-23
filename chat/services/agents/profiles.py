"""Perfis de agentes: exemplos idempotentes, publicação e completude (AG-1).

Sem geração paga, sem permissões automáticas, sem escolha silenciosa de
modelo: exemplos nascem como rascunho sem modelo (incompletos até o usuário
definir modelo e publicar).
"""

from __future__ import annotations

from django.db import transaction

from chat.models_agents import AgentDefinition, AgentVersion

EXAMPLE_PROFILES = (
    {
        "name": "Geral",
        "kind": "general",
        "description": "Responde e usa ferramentas autorizadas quando necessário.",
        "task_instructions": "Responda com precisão. Use ferramentas autorizadas "
        "apenas quando necessário; sem delegação.",
        "allowed_tools": [],
        "allowed_kb_ids": [],
        "delegatable_ids": [],
        "max_child_runs": 0,
    },
    {
        "name": "Pesquisador",
        "kind": "researcher",
        "description": "Busca evidências nas bases selecionadas e explica lacunas.",
        "task_instructions": "Busque evidências apenas nas bases selecionadas. "
        "Explique lacunas; sem web implícita; só leitura.",
        "allowed_tools": ["local:search_knowledge_base", "local:read_knowledge_excerpt"],
        "allowed_kb_ids": [],
        "delegatable_ids": [],
        "max_child_runs": 0,
    },
    {
        "name": "Revisor",
        "kind": "reviewer",
        "description": "Verifica afirmações, referências e contradições.",
        "task_instructions": "Verifique afirmações contra evidências. Só leitura; "
        "não aprove ações nem certifique segurança.",
        "allowed_tools": ["local:read_knowledge_excerpt"],
        "allowed_kb_ids": [],
        "delegatable_ids": [],
        "max_child_runs": 0,
    },
    {
        "name": "Coordenador",
        "kind": "coordinator",
        "description": "Delimita subtarefas, consulta especialistas e sintetiza.",
        "task_instructions": "Responda direto quando simples. Delegue no máximo "
        "duas subtarefas independentes a Pesquisador/Revisor e sintetize.",
        "allowed_tools": [],
        "allowed_kb_ids": [],
        "delegatable_ids": ["Pesquisador", "Revisor"],
        "max_child_runs": 2,
    },
)


def is_complete(version: AgentVersion) -> bool:
    """Completo = modelo definido (nunca escolhemos silenciosamente)."""
    return bool((version.model or "").strip())


def _resolve_delegatable(owner, names: list[str]) -> list[str]:
    uuids = []
    for name in names:
        try:
            d = AgentDefinition.objects.get(owner=owner, name=name)
        except AgentDefinition.DoesNotExist:
            continue
        uuids.append(str(d.uuid))
    return uuids


@transaction.atomic
def ensure_examples(owner) -> dict:
    """Cria os 4 perfis + rascunho r1 quando ausentes. Idempotente."""
    created_defs, created_vers = 0, 0
    for spec in EXAMPLE_PROFILES:
        definition, def_created = AgentDefinition.objects.get_or_create(
            owner=owner,
            name=spec["name"],
            defaults={"description": spec["description"], "kind": spec["kind"]},
        )
        created_defs += int(def_created)
        version, ver_created = AgentVersion.objects.get_or_create(
            definition=definition,
            revision=1,
            defaults={
                "task_instructions": spec["task_instructions"],
                "model": "",
                "allowed_tools": list(spec["allowed_tools"]),
                "allowed_kb_ids": list(spec["allowed_kb_ids"]),
                "delegatable_ids": _resolve_delegatable(owner, spec["delegatable_ids"]),
                "max_child_runs": spec["max_child_runs"],
                "published": False,
            },
        )
        created_vers += int(ver_created)
    # Coordenador referencia os demais: segunda passada resolve pendências.
    coord = AgentDefinition.objects.filter(owner=owner, name="Coordenador").first()
    if coord is not None:
        v1 = coord.versions.filter(revision=1, published=False).first()
        if v1 is not None and not v1.delegatable_ids:
            v1.delegatable_ids = _resolve_delegatable(owner, ["Pesquisador", "Revisor"])
            v1.save(update_fields=["delegatable_ids"])
    return {"definitions": created_defs, "versions": created_vers}


@transaction.atomic
def publish_version(version_id: int, owner) -> AgentVersion:
    """Publica r1 (ou outra revisão): congela como publicada."""
    version = AgentVersion.objects.select_related("definition").get(
        pk=version_id, definition__owner=owner
    )
    if not is_complete(version):
        raise ValueError("Defina o modelo antes de publicar.")
    version.published = True
    version.save(update_fields=["published"])
    return version


@transaction.atomic
def new_draft(definition_id: int, owner) -> AgentVersion:
    """Nova revisão rascunho copiada da última (publicadas nunca editadas)."""
    definition = AgentDefinition.objects.get(pk=definition_id, owner=owner)
    last = definition.versions.order_by("-revision").first()
    revision = (last.revision + 1) if last else 1
    fields = (
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
    data = {f: getattr(last, f) for f in fields} if last else {}
    return AgentVersion.objects.create(
        definition=definition, revision=revision, published=False, **data
    )
