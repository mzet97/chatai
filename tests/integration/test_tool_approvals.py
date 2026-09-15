"""M2: escrita local com aprovação persistente, retomada e idempotência (T4/T5)."""

import pytest
from asgiref.sync import sync_to_async

from chat.services.tools import approvals, executor
from chat.services.tools.context import ExecutionContext

pytestmark = pytest.mark.django_db(transaction=True)
adb = sync_to_async


def _ctx(user, conversation):
    return ExecutionContext(user_id=user.pk, conversation_id=conversation.pk)


async def _authed_call(name, args, ctx, tool_use_id="tu_1"):
    """Fluxo M2 completo: registra intenção → exige aprovação → executa."""
    return await executor.execute_authorized(
        name, args, ctx, tool_use_id=tool_use_id, approval=None
    )


async def test_escrita_aguarda_aprovacao(user, conversation):
    out = await _authed_call(
        "local__create_study_note", {"title": "T", "text": "corpo"}, _ctx(user, conversation)
    )
    assert out["ok"] is False and out["error"]["code"] == "approval_required"
    assert out["approval_id"]  # cliente solicita decisão nesse ID
    n = await adb(
        lambda: __import__("chat.models_tools", fromlist=["StudyNote"]).StudyNote.objects.count()
    )()
    assert n == 0  # nada escrito sem decisão


async def test_aprovar_uma_vez_executa_e_consulta(user, conversation, logged_client):
    from chat.models_tools import StudyNote

    out = await _authed_call(
        "local__create_study_note",
        {"title": "Revisão", "text": "conteúdo"},
        _ctx(user, conversation),
    )
    approval_id = out["approval_id"]
    decided = await adb(approvals.decide)(approval_id, "approve", user, idempotency_key="dec-1")
    assert decided["consumed"] is True
    done = await executor.execute_authorized(
        "local__create_study_note",
        {"title": "Revisão", "text": "conteúdo"},
        _ctx(user, conversation),
        tool_use_id="tu_1",
        approval=decided,
    )
    assert done["ok"] is True
    assert await adb(lambda: StudyNote.objects.filter(title="Revisão").count())() == 1
    listed = await executor.execute(
        "local__list_study_notes", {"limit": 10}, _ctx(user, conversation)
    )
    assert listed["ok"] and "Revisão" in listed["text"]


async def test_recusa_nao_escreve(user, conversation):
    out = await _authed_call(
        "local__create_study_note", {"title": "X", "text": "y"}, _ctx(user, conversation)
    )
    decided = await adb(approvals.decide)(out["approval_id"], "deny", user, idempotency_key="dec-2")
    done = await executor.execute_authorized(
        "local__create_study_note",
        {"title": "X", "text": "y"},
        _ctx(user, conversation),
        tool_use_id="tu_1",
        approval=decided,
    )
    assert done["ok"] is False and done["error"]["code"] == "denied"
    from chat.models_tools import StudyNote

    assert await adb(lambda: StudyNote.objects.count())() == 0


async def test_texto_no_chat_e_json_adulterado_nao_aprovam(user, conversation):
    """Só o endpoint decide; digest amarra os args exatos."""
    out = await _authed_call(
        "local__create_study_note", {"title": "A", "text": "original"}, _ctx(user, conversation)
    )
    # Tentativa de executar OUTROS args com a mesma aprovação: bloqueado.
    decided = await adb(approvals.decide)(
        out["approval_id"], "approve", user, idempotency_key="dec-3"
    )
    done = await executor.execute_authorized(
        "local__create_study_note",
        {"title": "A", "text": "ADULTERADO"},
        _ctx(user, conversation),
        tool_use_id="tu_1",
        approval=decided,
    )
    assert done["ok"] is False and done["error"]["code"] == "approval_required"


async def test_aprovacao_expirada_bloqueia(user, conversation):
    from datetime import timedelta

    from django.utils import timezone

    from chat.models_tools import ToolApproval

    out = await _authed_call(
        "local__create_study_note", {"title": "E", "text": "e"}, _ctx(user, conversation)
    )
    await adb(
        lambda: ToolApproval.objects.filter(public_id=out["approval_id"]).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
    )()
    with pytest.raises(approvals.Expired):
        await adb(approvals.decide)(out["approval_id"], "approve", user, idempotency_key="d")
    from chat.models_tools import StudyNote

    assert await adb(lambda: StudyNote.objects.count())() == 0


async def test_duplo_clique_consume_uma_vez(user, conversation):
    from chat.models_tools import StudyNote

    out = await _authed_call(
        "local__create_study_note", {"title": "D", "text": "d"}, _ctx(user, conversation)
    )
    first = await adb(approvals.decide)(
        out["approval_id"], "approve", user, idempotency_key="same-key"
    )
    second = await adb(approvals.decide)(
        out["approval_id"], "approve", user, idempotency_key="same-key"
    )
    assert first["consumed"] and second["consumed"]  # idempotente, mesmo efeito
    for tid in ("tu_1", "tu_1"):
        await executor.execute_authorized(
            "local__create_study_note",
            {"title": "D", "text": "d"},
            _ctx(user, conversation),
            tool_use_id=tid,
            approval=first,
        )
    assert await adb(lambda: StudyNote.objects.filter(title="D").count())() == 1
    with pytest.raises(approvals.AlreadyConsumed):
        await adb(approvals.decide)(
            out["approval_id"], "approve", user, idempotency_key="other-key"
        )


async def test_retomada_apos_reinicio_usa_linha_persistida(user, conversation):
    """Aprovação sobrevive a reinício: re-lida do banco, dentro da validade."""
    out = await _authed_call(
        "local__create_study_note", {"title": "R", "text": "r"}, _ctx(user, conversation)
    )
    decided = await adb(approvals.decide)(
        out["approval_id"], "approve", user, idempotency_key="dec-r"
    )
    reloaded = await adb(approvals.load)(decided["approval_id"])
    assert reloaded["decision"] == "approve" and reloaded["consumed"] is True
    done = await executor.execute_authorized(
        "local__create_study_note",
        {"title": "R", "text": "r"},
        _ctx(user, conversation),
        tool_use_id="tu_9",
        approval=reloaded,
    )
    assert done["ok"] is True
