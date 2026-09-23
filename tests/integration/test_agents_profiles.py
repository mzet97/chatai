"""M1: exemplos idempotentes, publicação e completude."""

import pytest

from chat.models_agents import AgentDefinition, AgentVersion
from chat.services.agents import profiles

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_user("agui", password="x")


def test_ensure_examples_idempotente(owner):
    first = profiles.ensure_examples(owner)
    assert first == {"definitions": 4, "versions": 4}
    second = profiles.ensure_examples(owner)
    assert second == {"definitions": 0, "versions": 0}
    assert AgentDefinition.objects.filter(owner=owner).count() == 4
    # Exemplos nascem incompletos: sem modelo, sem permissões automáticas.
    for v in AgentVersion.objects.filter(definition__owner=owner):
        assert profiles.is_complete(v) is False
        assert v.published is False


def test_publicar_exige_modelo(owner):
    profiles.ensure_examples(owner)
    geral = AgentDefinition.objects.get(owner=owner, name="Geral")
    v1 = geral.versions.get(revision=1)
    with pytest.raises(ValueError):
        profiles.publish_version(v1.pk, owner)
    v1.model = "claude-haiku-4-5-20251001"
    v1.save()
    profiles.publish_version(v1.pk, owner)
    assert AgentVersion.objects.get(pk=v1.pk).published is True


def test_coordenador_referencia_especialistas(owner):
    profiles.ensure_examples(owner)
    coord = AgentDefinition.objects.get(owner=owner, name="Coordenador")
    v1 = coord.versions.get(revision=1)
    nomes = set(
        AgentDefinition.objects.filter(uuid__in=v1.delegatable_ids).values_list("name", flat=True)
    )
    assert nomes == {"Pesquisador", "Revisor"}


def test_novo_rascunho_da_publicada(owner):
    profiles.ensure_examples(owner)
    geral = AgentDefinition.objects.get(owner=owner, name="Geral")
    v1 = geral.versions.get(revision=1)
    v1.model = "m"
    v1.save()
    profiles.publish_version(v1.pk, owner)
    v2 = profiles.new_draft(geral.pk, owner)
    assert (v2.revision, v2.published, v2.model) == (2, False, "m")
