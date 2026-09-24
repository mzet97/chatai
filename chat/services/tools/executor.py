"""Validação e execução de chamadas (T3/T6). Sequencial e determinístico."""

from __future__ import annotations

import asyncio
from typing import Any

from chat.services.anthropic_client import classify_error
from chat.services.tools import limits
from chat.services.tools.context import ExecutionContext
from chat.services.tools.local_tools import HANDLERS
from chat.services.tools.registry import ToolRecord, find_by_anthropic_name


def validate_args(schema: dict, args: Any) -> str | None:
    """Subconjunto JSON Schema suficiente p/ nossos schemas. Retorna erro ou None."""
    if not isinstance(args, dict):
        return "Argumentos devem ser um objeto."
    if schema.get("type") == "object" and not isinstance(args, dict):
        return "Argumentos devem ser um objeto."
    props = schema.get("properties", {})
    for req in schema.get("required", []):
        if req not in args:
            return f"Campo obrigatório ausente: {req!r}."
    if schema.get("additionalProperties") is False:
        for key in args:
            if key not in props:
                return f"Campo desconhecido: {key!r}."
    for key, subschema in props.items():
        if key not in args:
            continue
        err = _check_value(subschema, args[key], key)
        if err:
            return err
    return None


def _check_value(subschema: dict, value: Any, key: str) -> str | None:
    expected = subschema.get("type")
    if expected == "string" and not isinstance(value, str):
        return f"Campo {key!r} deve ser texto."
    if expected == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        return f"Campo {key!r} deve ser número."
    if expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        return f"Campo {key!r} deve ser inteiro."
    if "enum" in subschema and value not in subschema["enum"]:
        return f"Campo {key!r} fora do permitido."
    return None


def normalize_result(text: str) -> tuple[str, bool]:
    """Corta em 32 KiB com marcador; nunca grava ilimitado p/ truncar na tela."""
    raw = text.encode("utf-8")
    if len(raw) <= limits.MAX_RESULT_BYTES:
        return text, False
    cut = raw[: limits.MAX_RESULT_BYTES].decode("utf-8", errors="ignore")
    return cut + "\n[…resultado truncado em 32 KiB…]", True


async def execute(
    anthropic_name: str,
    args: Any,
    ctx: ExecutionContext,
    records: list[ToolRecord] | None = None,
    mcp_call=None,
) -> dict:
    """Executa UMA chamada validada. Retorna {ok, text?|error} — nunca levanta
    por causa de args inválidos, ferramenta desconhecida ou timeout.
    `mcp_call(name, args)` injeta o transporte MCP (stdio/HTTP)."""
    from chat.services.tools.local_tools import all_records

    recs = records if records is not None else all_records()
    rec = find_by_anthropic_name(recs, anthropic_name)
    if rec is None:
        return unknown_tool(anthropic_name)
    if rec.approval == "deny":
        return denied(rec)
    schema_err = validate_args(rec.input_schema, args)
    if schema_err:
        return {
            "ok": False,
            "error": {"code": "invalid_args", "message": schema_err},
        }
    if rec.origin == "mcp":
        if mcp_call is None:
            return {
                "ok": False,
                "error": {"code": "unavailable", "message": "Sem transporte MCP."},
            }
        try:
            result = await asyncio.wait_for(
                mcp_call(anthropic_name, args), timeout=limits.TOOL_TIMEOUT_S
            )
        except TimeoutError:
            return {
                "ok": False,
                "error": {"code": "timeout", "message": "Ferramenta excedeu o tempo."},
            }
        except Exception as exc:
            code, message = classify_error(exc)
            return {"ok": False, "error": {"code": code, "message": message}}
        if not result.get("ok"):
            return result
        text, truncated = normalize_result(str(result.get("text", "")))
        out: dict[str, Any] = {"ok": True, "text": text}
        if truncated:
            out["truncated"] = True
        images = _validated_result_images(rec, result)
        if images is not None:
            if isinstance(images, dict):  # erro de gate/validação
                return images
            out["images"] = images
        return out
    handler = HANDLERS.get(anthropic_name)
    if handler is None:
        return {
            "ok": False,
            "error": {"code": "unavailable", "message": "Ferramenta sem executor."},
        }
    try:
        result = await asyncio.wait_for(handler(args, ctx), timeout=limits.TOOL_TIMEOUT_S)
    except TimeoutError:
        return {
            "ok": False,
            "error": {"code": "timeout", "message": "Ferramenta excedeu o tempo."},
        }
    except Exception as exc:
        code, message = classify_error(exc)
        return {"ok": False, "error": {"code": code, "message": message}}
    if not result.get("ok"):
        return result
    text, truncated = normalize_result(str(result.get("text", "")))
    out: dict[str, Any] = {"ok": True, "text": text}
    if truncated:
        out["truncated"] = True
    images = _validated_result_images(rec, result)
    if images is not None:
        if isinstance(images, dict):  # erro de gate/validação
            return images
        out["images"] = images
    return out


def _validated_result_images(rec, result: dict):
    """Gate de capacidade + validação binária (M4/TV-5.2).

    Sem chave `images` no resultado: None (caminho de texto inalterado).
    Com imagens e sem capacidade correspondente: dict de erro
    `images_not_supported`. Com capacidade: lista canônica validada
    (teto binário próprio; Base64 nunca truncado) ou dict de erro
    `invalid_images`.
    """
    if not result.get("images"):
        return None
    if not rec.supports_images:
        return {
            "ok": False,
            "error": {
                "code": "images_not_supported",
                "message": "Ferramenta sem capacidade de resultado em imagem.",
            },
        }
    from chat.services import images as _images

    try:
        return _images.validate_tool_result_images(result.get("images"))
    except ValueError as exc:
        return {"ok": False, "error": {"code": "invalid_images", "message": str(exc)}}


async def execute_authorized(
    anthropic_name: str,
    args: Any,
    ctx: ExecutionContext,
    *,
    tool_use_id: str,
    approval: dict | None,
    records: list[ToolRecord] | None = None,
    run_uuid: str = "",
    mcp_call=None,
) -> dict:
    """Fluxo M2: valida → exige aprovação quando `require` → executa.

    Sem aprovação válida retorna `approval_required` + `approval_id` (a linha é
    criada/reutilizada aqui); nunca executa escrita sem decisão consumida.
    `mcp_call` é seam de teste; em produção o transporte MCP é resolvido da
    conexão do registro (T6) — sem isso a ferramenta morria em `unavailable`."""
    from asgiref.sync import sync_to_async

    from chat.services.tools import approvals as _approvals
    from chat.services.tools.local_tools import all_records

    recs = records if records is not None else all_records()
    rec = find_by_anthropic_name(recs, anthropic_name)
    if rec is None:
        return unknown_tool(anthropic_name)
    if rec.approval == "deny":
        return denied(rec)
    schema_err = validate_args(rec.input_schema, args)
    if schema_err:
        return {
            "ok": False,
            "error": {"code": "invalid_args", "message": schema_err},
        }
    if (
        approval is not None
        and approval.get("decision") == "deny"
        and approval.get("args_digest") == _approvals.args_digest(rec.stable_id, rec.version, args)
    ):
        return denied(rec)
    if rec.approval == "require" and not _approvals.valid_for(rec, args, approval):
        row = await sync_to_async(_approvals.request_approval)(
            owner=await sync_to_async(_owner_of)(ctx.user_id),
            conversation=await sync_to_async(_conversation_of)(ctx.conversation_id),
            run_uuid=run_uuid,
            tool_use_id=tool_use_id,
            record=rec,
            args=args,
        )
        return {
            "ok": False,
            "error": {
                "code": "approval_required",
                "message": "Escrita exige aprovação única neste chat.",
            },
            "approval_id": str(row.public_id),
        }
    ctx_with_id = ExecutionContext(
        user_id=ctx.user_id, conversation_id=ctx.conversation_id, tool_use_id=tool_use_id
    )
    if rec.origin == "mcp" and mcp_call is None:
        from chat.services.tools.discovery import make_mcp_call

        mcp_call = make_mcp_call(rec, user_id=ctx.user_id)
    return await execute(anthropic_name, args, ctx_with_id, recs, mcp_call=mcp_call)


def _owner_of(user_id: int):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.get(pk=user_id)


def _conversation_of(conversation_id: int):
    from chat.models import Conversation

    return Conversation.objects.get(pk=conversation_id)


def unknown_tool(name: str) -> dict:
    return {
        "ok": False,
        "error": {"code": "unknown_tool", "message": f"Ferramenta desconhecida: {name}."},
    }


def denied(rec: ToolRecord) -> dict:
    return {
        "ok": False,
        "error": {"code": "denied", "message": f"Ferramenta bloqueada: {rec.anthropic_name}."},
    }
