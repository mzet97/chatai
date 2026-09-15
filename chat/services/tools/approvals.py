"""Aprovações persistentes: expiração, consumo único atômico, digest de args."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from chat.models_tools import ToolApproval

APPROVAL_TTL = timedelta(minutes=10)


class Expired(Exception):
    pass


class AlreadyConsumed(Exception):
    pass


class NotFound(Exception):
    pass


def args_digest(stable_id: str, version: str, args: dict) -> str:
    canonical = json.dumps(
        {"tool": stable_id, "version": version, "args": args},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def request_approval(
    *,
    owner,
    conversation,
    run_uuid: str,
    tool_use_id: str,
    record,
    args: dict,
) -> ToolApproval:
    """Cria (ou reutiliza pendente idêntica) a linha de decisão."""
    digest = args_digest(record.stable_id, record.version, args)
    preview = json.dumps(args, ensure_ascii=False, sort_keys=True)[:500]
    with transaction.atomic():
        existing = (
            ToolApproval.objects.filter(
                owner=owner,
                conversation=conversation,
                run_uuid=run_uuid,
                tool_use_id=tool_use_id,
                decision="pending",
                args_digest=digest,
                expires_at__gt=timezone.now(),
            )
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            return existing
        return ToolApproval.objects.create(
            owner=owner,
            conversation=conversation,
            run_uuid=run_uuid,
            tool_use_id=tool_use_id,
            anthropic_name=record.anthropic_name,
            tool_version=record.version,
            args_digest=digest,
            args_preview=preview,
            expires_at=timezone.now() + APPROVAL_TTL,
        )


def decide(public_id: str, decision: str, user, *, idempotency_key: str | None) -> dict:
    """Consome UMA vez (UPDATE atômico). Mesma chave = idempotente; outra chave
    após consumo = AlreadyConsumed. Expirada = Expired."""
    if decision not in ("approve", "deny"):
        raise ValueError(f"Decisão inválida: {decision!r}.")
    now = timezone.now()
    with transaction.atomic():
        try:
            row = ToolApproval.objects.get(public_id=public_id, owner=user)
        except ToolApproval.DoesNotExist as exc:
            raise NotFound(f"Aprovação inexistente: {public_id}.") from exc
        if row.idempotency_key == idempotency_key and row.consumed_at is not None:
            return _as_dict(row)  # replay idempotente da mesma decisão
        if row.consumed_at is not None:
            raise AlreadyConsumed("Aprovação já consumida.")
        if row.expires_at <= now:
            raise Expired("Aprovação expirada; solicite nova decisão.")
        updated = ToolApproval.objects.filter(pk=row.pk, consumed_at__isnull=True).update(
            decision=decision,
            idempotency_key=idempotency_key,
            consumed_at=now,
            decided_at=now,
        )
        if not updated:
            raise AlreadyConsumed("Aprovação já consumida (concorrência).")
        row.refresh_from_db()
        return _as_dict(row)


def load(public_id: str) -> dict:
    """Re-lê do banco (retomada pós-reinício); nunca estado em memória."""
    try:
        row = ToolApproval.objects.get(public_id=public_id)
    except ToolApproval.DoesNotExist as exc:
        raise NotFound(f"Aprovação inexistente: {public_id}.") from exc
    return _as_dict(row)


def _as_dict(row: ToolApproval) -> dict:
    return {
        "approval_id": str(row.public_id),
        "decision": row.decision,
        "consumed": row.consumed_at is not None,
        "expired": row.expires_at <= timezone.now(),
        "args_digest": row.args_digest,
        "anthropic_name": row.anthropic_name,
        "tool_version": row.tool_version,
    }


def valid_for(record, args: dict, approval: dict | None) -> bool:
    """Aprovação vale se: consumida como approve + digest dos args exatos."""
    if not approval or approval.get("decision") != "approve":
        return False
    if not approval.get("consumed"):
        return False
    return approval.get("args_digest") == args_digest(record.stable_id, record.version, args)
