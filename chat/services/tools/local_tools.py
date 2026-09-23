"""Ferramentas locais M1: leitura e cálculo (T1). Sem eval/exec/shell/SQL."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from chat.services.tools.context import ExecutionContext
from chat.services.tools.registry import ToolRecord

APP_TIMEZONE = "America/Sao_Paulo"
_MAX_ABS = 1e12

_calculate_schema: dict[str, Any] = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "enum": ["add", "sub", "mul", "div"]},
        "a": {"type": "number"},
        "b": {"type": "number"},
    },
    "required": ["op", "a", "b"],
    "additionalProperties": False,
}

_current_time_schema: dict[str, Any] = {
    "type": "object",
    "properties": {"timezone": {"type": "string"}},
    "required": [],
    "additionalProperties": False,
}


def _num(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Número inválido: {value!r}.")
    number = float(value)
    if not math.isfinite(number) or abs(number) > _MAX_ABS:
        raise ValueError(f"Número fora dos limites: {value!r}.")
    return number


def _fmt(number: float) -> str:
    if number.is_integer() and abs(number) < 1e15:
        return str(int(number))
    return repr(number)


async def _calculate(args: dict, ctx: ExecutionContext) -> dict:
    try:
        a, b = _num(args["a"]), _num(args["b"])
    except (KeyError, ValueError) as exc:
        return {"ok": False, "error": {"code": "invalid_args", "message": str(exc)}}
    op = args.get("op")
    if op == "add":
        result = a + b
    elif op == "sub":
        result = a - b
    elif op == "mul":
        result = a * b
    elif op == "div":
        if b == 0:
            return {
                "ok": False,
                "error": {"code": "division_by_zero", "message": "Divisão por zero."},
            }
        result = a / b
    else:
        return {
            "ok": False,
            "error": {"code": "invalid_args", "message": f"Operação inválida: {op!r}."},
        }
    if not math.isfinite(result) or abs(result) > _MAX_ABS * 10:
        return {
            "ok": False,
            "error": {"code": "overflow", "message": "Resultado fora dos limites."},
        }
    return {"ok": True, "text": _fmt(result)}


async def _current_time(args: dict, ctx: ExecutionContext) -> dict:
    name = args.get("timezone") or APP_TIMEZONE
    if not isinstance(name, str) or name not in available_timezones():
        return {
            "ok": False,
            "error": {"code": "invalid_args", "message": f"Fuso inválido: {name!r}."},
        }
    try:
        now = datetime.now(ZoneInfo(name))
    except ZoneInfoNotFoundError:
        return {
            "ok": False,
            "error": {"code": "invalid_args", "message": f"Fuso inválido: {name!r}."},
        }
    return {"ok": True, "text": f"{now.isoformat(timespec='seconds')} ({name})"}


TITLE_MAX = 120
TEXT_MAX = 4000
LIST_MAX = 50

_create_note_schema: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "text": {"type": "string"},
    },
    "required": ["title", "text"],
    "additionalProperties": False,
}

_list_notes_schema: dict[str, Any] = {
    "type": "object",
    "properties": {"limit": {"type": "integer"}},
    "required": [],
    "additionalProperties": False,
}


def _operation_key(ctx: ExecutionContext, tool_use_id: str) -> str:
    return f"local:{ctx.conversation_id}:{tool_use_id}"


def _create_study_note_sync(
    *, owner_id: int, conversation_id: int, title: str, text: str, operation_key: str
) -> tuple[bool, str]:
    """Transação curta: nota + unicidade da operação (duplo clique = 1 nota)."""
    from django.db import IntegrityError, transaction

    from chat.models import Conversation
    from chat.models_tools import StudyNote

    title, text = title.strip(), text.strip()
    if not title or len(title) > TITLE_MAX:
        return False, f"Título com 1–{TITLE_MAX} caracteres."
    if not text or len(text) > TEXT_MAX:
        return False, f"Texto com 1–{TEXT_MAX} caracteres."
    if not Conversation.objects.filter(pk=conversation_id, owner_id=owner_id).exists():
        return False, "Conversa inválida para este usuário."
    try:
        with transaction.atomic():
            note, created = StudyNote.objects.get_or_create(
                operation_key=operation_key,
                defaults={
                    "owner_id": owner_id,
                    "conversation_id": conversation_id,
                    "title": title,
                    "text": text,
                },
            )
    except IntegrityError:
        return False, "Operação já registrada (concorrência)."
    return True, f"Nota {'criada' if created else 'já existente'}: {note.title}"


async def _create_study_note(args: dict, ctx: ExecutionContext) -> dict:
    from asgiref.sync import sync_to_async

    op_key = _operation_key(ctx, ctx.tool_use_id)
    ok, text = await sync_to_async(_create_study_note_sync)(
        owner_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        title=args.get("title", ""),
        text=args.get("text", ""),
        operation_key=op_key,
    )
    if not ok:
        return {"ok": False, "error": {"code": "invalid_args", "message": text}}
    return {"ok": True, "text": text}


async def _list_study_notes(args: dict, ctx: ExecutionContext) -> dict:
    from asgiref.sync import sync_to_async

    limit = args.get("limit", 10)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= LIST_MAX:
        return {
            "ok": False,
            "error": {"code": "invalid_args", "message": f"Limite 1–{LIST_MAX}."},
        }

    def _query():
        from chat.models_tools import StudyNote

        rows = StudyNote.objects.filter(
            owner_id=ctx.user_id, conversation_id=ctx.conversation_id
        ).order_by("-created_at")[:limit]
        return [(n.title, n.text[:200]) for n in rows]

    rows = await sync_to_async(_query)()
    if not rows:
        return {"ok": True, "text": "Nenhuma nota nesta conversa."}
    lines = [f"{i + 1}. {title} — {snippet}" for i, (title, snippet) in enumerate(rows)]
    return {"ok": True, "text": "\n".join(lines)}


HANDLERS = {
    "local__calculate": _calculate,
    "local__current_time": _current_time,
    "local__create_study_note": _create_study_note,
    "local__list_study_notes": _list_study_notes,
}

try:
    from chat.services.rag.tools import HANDLERS as _RAG_HANDLERS
    from chat.services.rag.tools import tool_records as _rag_records

    HANDLERS.update(_RAG_HANDLERS)
    _RAG_RECORDS = _rag_records()
except ImportError:
    _RAG_RECORDS = []


def all_records() -> list[ToolRecord]:
    return [
        *_RAG_RECORDS,
        ToolRecord(
            stable_id="local:calculate",
            origin="local",
            original_name="calculate",
            description="Aritmética básica (add, sub, mul, div) com dois números.",
            input_schema=_calculate_schema,
            version="t1",
            approval="auto",
        ),
        ToolRecord(
            stable_id="local:current_time",
            origin="local",
            original_name="current_time",
            description="Hora atual do sistema em um fuso IANA (padrão da aplicação).",
            input_schema=_current_time_schema,
            version="t1",
            approval="auto",
        ),
        ToolRecord(
            stable_id="local:create_study_note",
            origin="local",
            original_name="create_study_note",
            description="Cria nota de estudo na conversa atual. Exige aprovação.",
            input_schema=_create_note_schema,
            version="t1",
            approval="require",
        ),
        ToolRecord(
            stable_id="local:list_study_notes",
            origin="local",
            original_name="list_study_notes",
            description="Lista notas de estudo da conversa atual.",
            input_schema=_list_notes_schema,
            version="t1",
            approval="auto",
        ),
    ]
