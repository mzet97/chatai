"""M1: modelos de agentes — definições, versões, runs e trilha."""

import pytest
from django.db import IntegrityError

from chat.models_agents import (
    AgentDefinition,
    AgentRun,
    AgentVersion,
    BudgetLedger,
    CacheObservation,
    RunEvent,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_user("agowner", password="x")


@pytest.fixture
def definition(owner):
    return AgentDefinition.objects.create(owner=owner, name="Geral", kind="general")


def test_nome_unico_por_dono_isolamento(definition, django_user_model):
    other = django_user_model.objects.create_user("agother", password="x")
    AgentDefinition.objects.create(owner=other, name="Geral")
    with pytest.raises(IntegrityError):
        AgentDefinition.objects.create(owner=definition.owner, name="Geral")


def test_versao_revisao_unica_e_arquivo(definition):
    AgentVersion.objects.create(definition=definition, revision=1, published=True)
    with pytest.raises(IntegrityError):
        AgentVersion.objects.create(definition=definition, revision=1)
    definition.archived = True
    definition.save()
    assert AgentDefinition.objects.get(pk=definition.pk).archived is True


def test_arvore_run_e_trilha(owner, definition, django_user_model):
    from chat.models import Conversation

    conv = Conversation.objects.create(owner=owner, title="t")
    root = AgentRun.objects.create(owner=owner, conversation=conv, task="responder")
    child = AgentRun.objects.create(
        owner=owner, conversation=conv, parent=root, depth=1, task="pesquisar"
    )
    assert list(root.children.all()) == [child]
    RunEvent.objects.create(run=root, seq=0, kind="started")
    with pytest.raises(IntegrityError):
        RunEvent.objects.create(run=root, seq=0, kind="duplicado")
    BudgetLedger.objects.create(root_run=root, run=child, kind="child_slot", tokens=0)
    CacheObservation.objects.create(run=root, step=0, mode="disabled", eligible=False)
    assert root.ledger_lines.count() == 1
    assert root.cache_observations.count() == 1
