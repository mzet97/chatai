"""M5: citações revalidadas entre agentes (§6).

O filho retorna produto de trabalho não confiável; o pai recebe somente
referências revalidadas contra chunks autorizados (dono + bases da
conversa). IDs inventados, chunks de outro dono/base e texto livre nunca
viram citação documental. Sem chamadas pagas, sem commit.
"""

import pytest

from chat.models import Conversation
from chat.models_rag import (
    Chunk,
    ConversationKnowledge,
    Document,
    DocumentVersion,
    KnowledgeBase,
)
from chat.services.agents import evidence, team

pytestmark = pytest.mark.django_db(transaction=True)


def _chunk(owner, base, text="trecho citável do manual"):
    from chat.services.rag import publish

    profile = publish.get_or_create_current_profile()
    doc = Document.objects.create(base=base, owner=owner, name="manual.txt")
    ver = DocumentVersion.objects.create(
        document=doc,
        number=1,
        sha256="cd" * 32,
        filename="manual.txt",
        size_bytes=len(text),
        rel_path=f"{doc.uuid}/manual.txt",
    )
    return Chunk.objects.create(
        version=ver,
        profile=profile,
        order=0,
        text=text,
        search_text=text,
        locator={"page": 3},
        token_count=10,
    )


@pytest.fixture
def scope(db, django_user_model):
    owner = django_user_model.objects.create_user("pai", password="pw")
    base = KnowledgeBase.objects.create(owner=owner, name="Manuais")
    conv = Conversation.objects.create(owner=owner, title="equipe")
    ConversationKnowledge.objects.create(conversation=conv, bases=[str(base.uuid)], mode="tools")
    chunk = _chunk(owner, base)
    other = django_user_model.objects.create_user("outro", password="pw")
    other_base = KnowledgeBase.objects.create(owner=other, name="Alheia")
    foreign = _chunk(other, other_base, text="trecho alheio")
    return {
        "owner": owner,
        "base": base,
        "conv": conv,
        "chunk": chunk,
        "foreign": foreign,
    }


def test_revalidate_aprova_so_chunk_autorizado(scope):
    refs = [
        {"chunk_uuid": str(scope["chunk"].uuid)},
        {"chunk_uuid": "00000000-0000-0000-0000-000000000000"},
        {"chunk_uuid": str(scope["foreign"].uuid)},
        "o revisor disse que está certo",
        {"sem": "chave"},
    ]
    out = evidence.revalidate(
        owner=scope["owner"],
        conversation=scope["conv"],
        refs=refs,
    )
    assert [v["chunk_uuid"] for v in out["valid"]] == [str(scope["chunk"].uuid)]
    assert out["valid"][0]["excerpt"].startswith("trecho citável")
    assert out["valid"][0]["locator"] == {"page": 3}
    assert len(out["dropped"]) == 4


def test_child_answer_block_so_cita_revalidado(scope):
    result = {
        "state": "done",
        "summary": "Prazo de 30 dias.",
        "evidence": [
            {"chunk_uuid": str(scope["chunk"].uuid)},
            {"chunk_uuid": str(scope["foreign"].uuid)},
            "o revisor disse",
        ],
        "limitations": "",
        "failure": "",
    }
    block = team.child_answer_block("tu1", result, owner=scope["owner"], conversation=scope["conv"])
    assert block["type"] == "tool_result"
    assert str(scope["chunk"].uuid)[:8] in block["content"]
    assert "manual.txt" in block["content"]
    assert "trecho citável do manual" in block["content"]
    assert str(scope["foreign"].uuid)[:8] not in block["content"]
    assert "o revisor disse" not in block["content"]
    assert "Prazo de 30 dias." in block["content"]


def test_child_answer_block_sem_escopo_mantem_texto_sem_citar(scope):
    # Compatibilidade: sem dono/conversa não há base para revalidar —
    # nada é apresentado como citação documental.
    result = {
        "state": "done",
        "summary": "Resumo.",
        "evidence": [{"chunk_uuid": str(scope["chunk"].uuid)}],
        "limitations": "",
        "failure": "",
    }
    block = team.child_answer_block("tu1", result)
    assert "Evidência revalidada" not in block["content"]
    assert str(scope["chunk"].uuid)[:8] not in block["content"]
    assert "Resumo." in block["content"]
