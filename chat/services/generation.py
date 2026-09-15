"""Orquestração das execuções (RF-11/12/13). Views finas; regras aqui.

Fluxo: POST reserva (transação curta) → GET /stream executa (SSE).
Toda escrita ORM ocorre em funções síncronas pequenas; o laço async nunca
segura transação aberta durante espera de rede.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time

from asgiref.sync import sync_to_async
from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone

from chat.models import Conversation, GenerationRun, Message
from chat.services import configuration as cfg
from chat.services.anthropic_client import ResolvedConfig, classify_error
from chat.services.context_builder import BudgetExceeded, CountFailed, build_context

CHECKPOINT_SECONDS = 2.0
CHECKPOINT_CHARS = 4000
STALE_HEARTBEAT_MINUTES = 5


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_title(text: str) -> str:
    """Regra local de título (RF-02): primeira linha, ~60 chars. Sem IA."""
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else "Nova conversa"
    return line[:60] if len(line) <= 60 else line[:57] + "..."


# --- reserva (síncrono; chamado pela view POST) ---


def reserve_run(
    *,
    conversation: Conversation,
    content: str,
    idempotency_key: str,
    user_message: Message | None = None,
) -> tuple[GenerationRun, bool, str | None]:
    """Reserva run `preparing` e adquire exclusividade (transação curta).

    Sem `user_message`: persiste a mensagem do usuário (envio novo).
    Com `user_message`: retry reutiliza a pergunta original, sem duplicá-la.
    Retorna (run, created, None). `created=False` = replay idempotente.
    Levanta `RunBusy` se outra execução ativa; `IdempotencyConflict` em hash distinto.
    """
    existing = GenerationRun.objects.filter(
        conversation=conversation, idempotency_key=idempotency_key
    ).first()
    if existing is not None:
        if existing.content_hash != content_hash(content):
            raise IdempotencyConflict("Mesma chave com conteúdo diferente.")
        return existing, False, None
    # Contenção SQLite ("database is locked") recebe retry curto e limitado;
    # esgotado o retry, vira RunBusy (erro recuperável, nunca trava a conversa).
    last_locked: OperationalError | None = None
    msg = user_message  # retry explícito reutiliza; envio novo cria (e descarta em rollback)
    for _ in range(6):
        try:
            with transaction.atomic():
                if msg is None:
                    seq = (Message.objects.filter(conversation=conversation).count()) + 1
                    msg = Message.objects.create(
                        conversation=conversation, seq=seq, role="user", text=content, state="ok"
                    )
                attempt = (
                    GenerationRun.objects.filter(
                        conversation=conversation, user_message=msg
                    ).count()
                    + 1
                )
                run = GenerationRun.objects.create(
                    conversation=conversation,
                    user_message=msg,
                    attempt=attempt,
                    idempotency_key=idempotency_key,
                    content_hash=content_hash(content),
                    state="preparing",
                    worker_pid=os.getpid(),
                )
                acquired = Conversation.objects.filter(
                    uuid=conversation.uuid, active_run__isnull=True
                ).update(active_run=run)
                if not acquired:
                    raise RunBusy("Já existe uma geração ativa nesta conversa.")
            break
        except (RunBusy, IdempotencyConflict):
            raise
        except IntegrityError as exc:
            raise RunBusy(f"Conflito de reserva: {exc}") from exc
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            last_locked = exc
            if user_message is None:
                msg = None  # descarta a mensagem da transação com rollback
            time.sleep(0.05)
    else:
        raise RunBusy(f"Conversa ocupada (contenção no banco): {last_locked}") from last_locked
    if (
        conversation.messages.filter(role="user").count() == 1
        and conversation.title == "Nova conversa"
    ):
        Conversation.objects.filter(pk=conversation.pk).update(title=make_title(content))
    return run, True, None


class RunBusy(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


# --- carregamento / contexto (leituras pequenas via sync_to_async) ---


def _load_run(run_id: str) -> GenerationRun:
    return GenerationRun.objects.select_related("conversation", "user_message").get(uuid=run_id)


def _history_for(conversation_id: int, upto_seq: int) -> list[dict]:
    rows = (
        Message.objects.filter(conversation_id=conversation_id, seq__lt=upto_seq)
        .order_by("seq")
        .values("seq", "role", "text", "state")
    )
    return list(rows)


def _resolve_snapshot_config(user, conversation) -> tuple[ResolvedConfig, cfg.Credential, str]:
    from chat.services.anthropic_client import resolve_for_user

    resolved, cred = resolve_for_user(user, conversation)
    return resolved, cred, cred.secret or ""


def _system_param(system: str):
    """A API exige system como array de blocos; vazio deve ser omitido (null dá 400)."""
    from anthropic import NOT_GIVEN

    if system:
        return [{"type": "text", "text": system}]
    return NOT_GIVEN


def _bind_counter(client):
    async def count(*, model: str, system: str, messages: list) -> int:
        result = await client.messages.count_tokens(
            model=model, system=_system_param(system), messages=messages
        )
        return result.input_tokens

    return count


# --- finalização (transação curta) ---


def _finalize(
    run_id: str,
    *,
    state: str,
    text: str = "",
    msg_state: str = "ok",
    usage: dict | None = None,
    stop_reason: str | None = None,
    truncated: bool = False,
    error_code: str | None = None,
    error_message: str | None = None,
    actual_model: str | None = None,
    request_id: str | None = None,
    response_id: str | None = None,
    context_used: list | None = None,
    snapshot: dict | None = None,
) -> None:
    with transaction.atomic():
        # Sem select_for_update: no SQLite ele não oferece bloqueio de linha (ADR-003).
        run = GenerationRun.objects.get(uuid=run_id)
        update = {"state": state, "finished_at": timezone.now()}
        if snapshot is not None:
            update["snapshot"] = snapshot
        if context_used is not None:
            update["context_used"] = context_used
        if actual_model:
            update["actual_model"] = actual_model
        if request_id:
            update["request_id"] = request_id
        if response_id:
            update["response_id"] = response_id
        if usage:
            # Uso final substitui contadores (nunca soma cumulativo — RF §9).
            update["input_tokens"] = usage.get("input_tokens")
            update["output_tokens"] = usage.get("output_tokens")
        if stop_reason:
            update["stop_reason"] = stop_reason
        update["truncated"] = truncated
        update["error_code"] = error_code
        update["error_message"] = (error_message or "")[:500] if error_message else None
        if run.assistant_message_id:
            Message.objects.filter(pk=run.assistant_message_id).update(text=text, state=msg_state)
        for k, v in update.items():
            setattr(run, k, v)
        run.save()
        Conversation.objects.filter(pk=run.conversation_id).update(active_run=None)


def _release_only(run_id: str) -> None:
    run = GenerationRun.objects.filter(uuid=run_id).first()
    if run:
        Conversation.objects.filter(pk=run.conversation_id, active_run__uuid=run_id).update(
            active_run=None
        )


def _checkpoint(run_id: str, text: str) -> None:
    """Checkpoint espaçado; retry curto em `database is locked`."""
    for _ in range(3):
        try:
            with transaction.atomic():
                run = GenerationRun.objects.get(uuid=run_id)
                if run.state == "cancelled" or run.cancel_requested:
                    return
                if run.assistant_message_id:
                    Message.objects.filter(pk=run.assistant_message_id).update(
                        text=text, state="partial"
                    )
                GenerationRun.objects.filter(pk=run.pk).update(last_heartbeat=timezone.now())
            return
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            time.sleep(0.2)


# --- execução (async; chamado pela view SSE) ---


async def execute_run(run_id: str, *, user, client=None):
    """Gerador async de eventos SSE (dicts). Nunca levanta após o primeiro evento:
    erros viram evento `error` + persistência."""
    from chat.services import delivery as _delivery
    from chat.services import variation as _variation
    from chat.services.anthropic_client import build_client as _build
    from chat.services.base_policy import POLICY_VERSION, compose_system

    _seq = 0

    def _emit(event: dict) -> dict:
        """Envelope: todo evento de dados carrega run_id + sequência monotônica."""
        nonlocal _seq
        _seq += 1
        return {"run_id": str(run_id), "seq": _seq, **event}

    run = await sync_to_async(_load_run)(run_id)
    conversation = run.conversation
    resolved, cred, secret = await sync_to_async(_resolve_snapshot_config)(user, conversation)
    snapshot = resolved.as_dict()
    snapshot["conversation_uuid"] = str(conversation.uuid)
    snapshot["system_prompt"] = conversation.system_prompt or ""
    snapshot["base_policy_version"] = POLICY_VERSION
    snapshot["strict_mode"] = conversation.strict_mode
    composed_system = compose_system(conversation.system_prompt or "")
    level = conversation.temperature_level or _variation.DEFAULT_LEVEL
    try:
        temp_value, temp_state, temp_reason = _variation.resolve_temperature(
            level, model=resolved.model, base_url=resolved.base_url
        )
    except ValueError:
        # Dado legado fora do enum: cai para o padrão em vez de quebrar a geração.
        level = _variation.DEFAULT_LEVEL
        temp_value, temp_state, temp_reason = _variation.resolve_temperature(
            level, model=resolved.model, base_url=resolved.base_url
        )
    snapshot["requested_level"] = level
    snapshot["temperature_sent"] = temp_value
    snapshot["temperature_state"] = temp_state
    snapshot["temperature_reason"] = temp_reason
    snapshot["temp_map_version"] = _variation.TEMP_MAP_VERSION
    # Entrega (streaming x completa): controla só o que chega ao navegador.
    # O provedor é sempre consumido pelo mesmo client.messages.stream; o modo
    # nunca altera modelo, temperatura, system, histórico ou limite de saída.
    requested_mode = conversation.response_mode or _delivery.STREAMING
    try:
        live_mode, mode_reason = _delivery.resolve_delivery(requested_mode)
    except ValueError:
        requested_mode = _delivery.STREAMING
        live_mode, mode_reason = _delivery.resolve_delivery(requested_mode)
    live = live_mode == _delivery.STREAMING
    snapshot["requested_response_mode"] = requested_mode
    snapshot["effective_response_mode"] = live_mode
    snapshot["response_mode_reason"] = mode_reason

    if client is None:
        if not secret:
            await sync_to_async(_finalize)(
                run_id,
                state="failed",
                msg_state="failed",
                snapshot=snapshot,
                error_code="unauthorized",
                error_message="Nenhuma chave configurada.",
            )
            yield _emit(
                {
                    "type": "error",
                    "code": "unauthorized",
                    "message": "Nenhuma chave configurada. Abra Configurações.",
                }
            )
            return
        try:
            base_url = cfg.validate_base_url(resolved.base_url)
        except ValueError as exc:
            await sync_to_async(_finalize)(
                run_id,
                state="failed",
                msg_state="failed",
                snapshot=snapshot,
                error_code="validation",
                error_message=str(exc),
            )
            yield _emit({"type": "error", "code": "validation", "message": str(exc)})
            return
        client = _build(
            api_key=secret,
            base_url=base_url,
            timeout_seconds=resolved.timeout_seconds,
            max_retries=resolved.max_retries,
        )

    history = await sync_to_async(_history_for)(conversation.id, run.user_message.seq)
    try:
        built = await build_context(
            history=history,
            current_text=run.user_message.text,
            system=composed_system,
            model=resolved.model,
            max_tokens=resolved.max_output_tokens,
            input_budget=resolved.input_budget,
            strict=conversation.strict_mode,
            count_tokens=_bind_counter(client),
        )
    except BudgetExceeded as exc:
        await sync_to_async(_finalize)(
            run_id,
            state="failed",
            msg_state="failed",
            snapshot=snapshot,
            error_code="context_too_large",
            error_message=str(exc),
        )
        yield _emit({"type": "error", "code": "context_too_large", "message": str(exc)})
        return
    except CountFailed as exc:
        await sync_to_async(_finalize)(
            run_id,
            state="failed",
            msg_state="failed",
            snapshot=snapshot,
            error_code="count_failed",
            error_message=f"Contagem de tokens falhou: {exc}",
        )
        yield _emit(
            {
                "type": "error",
                "code": "count_failed",
                "message": "Não foi possível validar o limite (contagem falhou). Tente de novo.",
            }
        )
        return

    context_used = [{"seq": s, "included": True} for s in built.included_seqs]
    context_used.append({"omitted_turns": built.omitted_turns})
    # Catálogo congelado por execução: interseção prefs ∩ disponível agora.
    # Vazio = caminho textual inalterado, sem MCP, sem payload de tools.
    from chat.services.tools import catalog as _tool_catalog

    tool_catalog = await sync_to_async(_tool_catalog.authorized_catalog)(conversation)
    snapshot["tools_enabled"] = [r.stable_id for r in tool_catalog]
    await sync_to_async(_set_streaming)(run_id, snapshot, context_used, resolved.model)
    yield _emit(
        {
            "type": "run_started",
            "conversation_id": str(conversation.uuid),
            "message_id": str(run.user_message.uuid),
            "model": resolved.model,
            "mode": live_mode,
            "requested_mode": requested_mode,
        }
    )

    if tool_catalog:
        extra_body = {"temperature": temp_value} if temp_value is not None else None
        async for _ev in _execute_tool_path(
            run_id,
            _emit,
            user=user,
            conversation=conversation,
            client=client,
            model=built.model,
            system=built.system,
            max_tokens=built.max_tokens,
            extra_body=extra_body,
            live=live,
            catalog=tool_catalog,
            snapshot=snapshot,
        ):
            yield _ev
        return

    text_parts: list[str] = []
    last_checkpoint = time.monotonic()
    # temperature SOMENTE quando permitido, resolvida no servidor; nunca None,
    # nunca via contagem (count_tokens monta seus próprios argumentos).
    stream_kwargs = {
        "model": built.model,
        "messages": built.messages,
        "system": _system_param(built.system),
        "max_tokens": built.max_tokens,
    }
    if temp_value is not None:
        stream_kwargs["extra_body"] = {"temperature": temp_value}
    try:
        async with client.messages.stream(**stream_kwargs) as stream:
            async for delta in stream.text_stream:
                # Cancelamento cooperativo: a view de cancelar seta o flag.
                if await sync_to_async(_cancel_flag)(run_id):
                    await stream.close()
                    await sync_to_async(_finalize)(
                        run_id,
                        state="cancelled",
                        text="".join(text_parts),
                        msg_state="cancelled",
                        error_code="cancelled",
                        error_message="Geração interrompida pelo usuário.",
                    )
                    yield _emit(
                        {
                            "type": "cancelled",
                            "message": "Geração interrompida pelo usuário.",
                        }
                    )
                    return
                text_parts.append(delta)
                if live:
                    yield _emit({"type": "text_delta", "text": delta})
                now = time.monotonic()
                current = "".join(text_parts)
                if now - last_checkpoint >= CHECKPOINT_SECONDS or len(
                    current
                ) % CHECKPOINT_CHARS < len(delta):
                    await sync_to_async(_checkpoint)(run_id, current)
                    last_checkpoint = now
            final = await stream.get_final_message()
            request_id = getattr(stream, "request_id", None)
    except asyncio.CancelledError:
        # Cliente desconectou (aba fechada) ou servidor encerrando: interrompida, nunca concluída.
        cancelled = await sync_to_async(_cancel_flag)(run_id)
        state = "cancelled" if cancelled else "interrupted"
        await sync_to_async(_finalize)(
            run_id,
            state=state,
            text="".join(text_parts),
            msg_state="cancelled",
            error_code=state,
            error_message="Leitura interrompida; parcial preservado.",
        )
        return
    except Exception as exc:  # erro no meio do stream → protocolo, não status HTTP
        code, message = classify_error(exc)
        await sync_to_async(_finalize)(
            run_id,
            state="failed",
            text="".join(text_parts),
            msg_state="failed" if not text_parts else "partial",
            error_code=code,
            error_message=f"{message} ({type(exc).__name__})",
        )
        yield _emit({"type": "error", "code": code, "message": message})
        return

    full_text, blocks, stop_reason, usage, actual_model, response_id = _parse_final(final)
    truncated = stop_reason == "max_tokens"
    try:
        await sync_to_async(_finalize)(
            run_id,
            state="done",
            text=full_text,
            msg_state="ok",
            usage=usage,
            stop_reason=stop_reason,
            truncated=truncated,
            actual_model=actual_model,
            request_id=request_id,
            response_id=response_id,
        )
    except Exception as exc:
        await sync_to_async(_finalize)(
            run_id,
            state="failed",
            text=full_text,
            msg_state="failed",
            error_code="persist_failed",
            error_message=f"Resposta recebida mas persistência falhou: {exc}",
        )
        yield _emit(
            {
                "type": "error",
                "code": "persist_failed",
                "message": "Resposta recebida, mas a persistência final falhou.",
            }
        )
        return
    if usage and (usage.get("input_tokens") is not None or usage.get("output_tokens") is not None):
        yield _emit(
            {
                "type": "usage",
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "final": True,
            }
        )
    # `done` é o estado canônico: carrega o texto final autorizado e persistido,
    # para o modo completo não depender de deltas que nunca foram enviados.
    yield _emit(
        {
            "type": "done",
            "message_id": await sync_to_async(_assistant_uuid)(run_id),
            "text": full_text,
            "stop_reason": stop_reason,
            "truncated": truncated,
            "model": actual_model or built.model,
            "input_tokens": (usage or {}).get("input_tokens"),
            "output_tokens": (usage or {}).get("output_tokens"),
        }
    )


def _parse_final(final) -> tuple[str, list, str | None, dict, str | None, str | None]:
    """Extrai texto de TODOS os blocos de texto (sem assumir content[0])."""
    texts, blocks = [], []
    for block in getattr(final, "content", None) or []:
        if getattr(block, "type", "") == "text":
            texts.append(getattr(block, "text", ""))
            blocks.append({"type": "text", "text": getattr(block, "text", "")})
    usage_obj = getattr(final, "usage", None)
    usage = (
        {
            "input_tokens": getattr(usage_obj, "input_tokens", None),
            "output_tokens": getattr(usage_obj, "output_tokens", None),
        }
        if usage_obj
        else {}
    )
    return (
        "".join(texts),
        blocks,
        getattr(final, "stop_reason", None),
        usage,
        getattr(final, "model", None),
        getattr(final, "id", None),
    )


def _set_streaming(run_id: str, snapshot: dict, context_used: list, requested_model: str) -> None:
    with transaction.atomic():
        run = GenerationRun.objects.get(uuid=run_id)
        seq = Message.objects.filter(conversation_id=run.conversation_id).count() + 1
        answer = Message.objects.create(
            conversation_id=run.conversation_id, seq=seq, role="assistant", text="", state="partial"
        )
        run.assistant_message = answer
        run.state = "streaming"
        run.snapshot = snapshot
        run.context_used = context_used
        run.requested_model = requested_model
        run.last_heartbeat = timezone.now()
        run.save()


def _assistant_uuid(run_id: str) -> str | None:
    run = GenerationRun.objects.filter(uuid=run_id).first()
    if run and run.assistant_message_id:
        msg = Message.objects.filter(pk=run.assistant_message_id).first()
        return str(msg.uuid) if msg else None
    return None


def _cancel_flag(run_id: str) -> bool:
    return GenerationRun.objects.filter(uuid=run_id, cancel_requested=True).exists()


def request_cancel(run_id: str) -> bool:
    """Marca cancelamento cooperativo. Retorna False se já terminal."""
    n = GenerationRun.objects.filter(
        uuid=run_id,
        cancel_requested=False,
        state__in=("preparing", "streaming", "awaiting_approval"),
    ).update(cancel_requested=True)
    return n > 0


def _set_paused(run_id: str, snapshot: dict, text: str) -> None:
    """Pausa humana: estado persistido, sem transação aberta, sem espera em memória."""
    with transaction.atomic():
        run = GenerationRun.objects.get(uuid=run_id)
        run.state = "awaiting_approval"
        run.snapshot = snapshot
        run.save(update_fields=["state", "snapshot"])
        if run.assistant_message_id:
            Message.objects.filter(pk=run.assistant_message_id).update(
                text=text, state="partial"
            )


def retry_info(*, conversation: Conversation, user_message: Message) -> tuple[bool, str]:
    """Retry explícito só do ÚLTIMO turno falho/cancelado/interrompido."""
    last_user = conversation.messages.filter(role="user").order_by("-seq").first()
    if last_user is None or last_user.pk != user_message.pk:
        return False, "Só é possível repetir o último turno da conversa."
    last_run = user_message.runs_as_prompt.order_by("-attempt").first()
    if last_run is None or last_run.state not in (
        "failed",
        "cancelled",
        "interrupted",
        "abandoned",
    ):
        return False, "O último turno não está em estado que permita repetição."
    if conversation.active_run_id is not None:
        return False, "Existe uma geração ativa nesta conversa."
    return True, ""


# --- caminho com ferramentas (M5) ---


async def _execute_tool_path(
    run_id, _emit, *, user, conversation, client, model, system, max_tokens,
    extra_body, live, catalog, snapshot,
):
    """Loop explícito com eventos finos; `done` só no turno inteiro."""
    from chat.services.tools.chat_loop import LoopState, run_tool_events
    from chat.services.tools.context import ExecutionContext

    ctx = ExecutionContext(user_id=user.pk, conversation_id=conversation.pk)
    state = LoopState(messages=[dict(m) for m in _tool_base_messages(snapshot)])
    agen = run_tool_events(
        client=client,
        model=model,
        system=system,
        max_tokens=max_tokens,
        state=state,
        catalog=catalog,
        ctx=ctx,
        owner=user,
        conversation=conversation,
        run_uuid=str(run_id),
        live=live,
        extra_body=extra_body,
        cancel_flag=lambda: sync_to_async(_cancel_flag)(run_id),
    )
    async for ev in agen:
        kind = ev["type"]
        if kind == "turn_paused":
            snapshot["tool_loop"] = {
                "pending": ev["pending"],
                "loop": ev["loop"],
                "model": model,
                "system": system,
                "max_tokens": max_tokens,
                "extra_body": extra_body,
            }
            text = "".join(ev["loop"]["text_parts"])
            await sync_to_async(_set_paused)(run_id, snapshot, text)
            yield _emit({"type": "run_paused", "state": "awaiting_approval"})
            return
        if kind == "turn_done":
            stopped = ev.get("stopped", "end_turn")
            text = ev.get("final_text", "")
            usage = ev.get("usage") or None
            if stopped == "end_turn":
                await sync_to_async(_finalize)(
                    run_id, state="done", text=text, msg_state="ok",
                    usage=usage, stop_reason=ev.get("stop_reason"),
                )
                if usage and (
                    usage.get("input_tokens") is not None
                    or usage.get("output_tokens") is not None
                ):
                    yield _emit(
                        {
                            "type": "usage",
                            "input_tokens": usage.get("input_tokens"),
                            "output_tokens": usage.get("output_tokens"),
                            "final": True,
                        }
                    )
                yield _emit(
                    {
                        "type": "done",
                        "message_id": await sync_to_async(_assistant_uuid)(run_id),
                        "text": text,
                        "stop_reason": ev.get("stop_reason"),
                        "truncated": False,
                        "model": model,
                        "input_tokens": (usage or {}).get("input_tokens"),
                        "output_tokens": (usage or {}).get("output_tokens"),
                    }
                )
            elif stopped == "cancelled":
                await sync_to_async(_finalize)(
                    run_id, state="cancelled", text=text, msg_state="cancelled",
                    error_code="cancelled",
                    error_message="Geração interrompida pelo usuário.",
                )
                yield _emit(
                    {"type": "cancelled", "message": "Geração interrompida pelo usuário."}
                )
            else:
                code = "tool_limit" if stopped == "limit" else "tool_budget"
                message = (
                    "Limite de etapas de ferramentas atingido."
                    if stopped == "limit"
                    else "Orçamento de tempo do ciclo de ferramentas esgotado."
                )
                await sync_to_async(_finalize)(
                    run_id, state="failed", text=text,
                    msg_state="failed" if not text else "partial",
                    error_code=code, error_message=message,
                )
                yield _emit({"type": "error", "code": code, "message": message})
            return
        yield _emit(ev)


def _tool_base_messages(snapshot: dict) -> list:
    return snapshot.get("tool_base_messages") or []


async def resume_run(run_id: str, *, user, client=None):
    """Continua execução pausada do estado persistido (sem re-perguntar).

    Revalida propriedade, prefs/catálogo, schema e aprovação antes do efeito.
    """
    from chat.services import delivery as _delivery
    from chat.services.tools import catalog as _tool_catalog
    from chat.services.tools.chat_loop import (
        LoopState,
        check_resume_entry,
        error_result,
        execute_one,
        run_tool_events,
    )
    from chat.services.tools.context import ExecutionContext

    _seq = 0

    def _emit(event: dict) -> dict:
        nonlocal _seq
        _seq += 1
        return {"run_id": str(run_id), "seq": _seq, **event}

    run = await sync_to_async(_load_run)(run_id)
    conversation = run.conversation
    if run.conversation.owner_id != user.pk:
        yield _emit({"type": "error", "code": "forbidden", "message": "Acesso negado."})
        return
    if run.state != "awaiting_approval":
        yield _emit(
            {"type": "error", "code": "validation", "message": "Execução não está pausada."}
        )
        return
    if await sync_to_async(_cancel_flag)(run_id):
        await sync_to_async(_finalize)(
            run_id, state="cancelled", msg_state="cancelled",
            error_code="cancelled", error_message="Geração interrompida pelo usuário.",
        )
        yield _emit(
                    {"type": "cancelled", "message": "Geração interrompida pelo usuário."}
                )
        return
    snap = run.snapshot or {}
    loop_snap = (snap.get("tool_loop") or {})
    pending = loop_snap.get("pending", [])
    state = LoopState.from_snapshot(loop_snap.get("loop", {}))
    catalog = await sync_to_async(_tool_catalog.authorized_catalog)(conversation)
    ctx = ExecutionContext(user_id=user.pk, conversation_id=conversation.pk)
    live = (snap.get("effective_response_mode") or _delivery.STREAMING) == _delivery.STREAMING
    if client is None:
        resolved, _cred, secret = await sync_to_async(_resolve_snapshot_config)(user, conversation)
        if not secret:
            yield _emit(
                {
                    "type": "error",
                    "code": "unauthorized",
                    "message": "Nenhuma chave configurada.",
                }
            )
            return
        from chat.services import configuration as _cfg
        from chat.services.anthropic_client import build_client as _build

        try:
            base_url = _cfg.validate_base_url(resolved.base_url)
        except ValueError as exc:
            yield _emit({"type": "error", "code": "validation", "message": str(exc)})
            return
        client = _build(
            api_key=secret,
            base_url=base_url,
            timeout_seconds=resolved.timeout_seconds,
            max_retries=resolved.max_retries,
        )
    await sync_to_async(_mark_streaming)(run_id)
    yield _emit(
        {"type": "run_started", "resumed": True, "mode": snap.get("effective_response_mode")}
    )
    # Grupo pausado: revalida cada item antes do efeito; sem re-perguntar.
    group_blocks: list[dict] = []
    for entry in pending:
        call = {
            "id": entry["tool_use_id"],
            "name": entry["name"],
            "input": entry.get("args", {}),
        }
        rec, approval, err = await sync_to_async(check_resume_entry)(
            entry, catalog, user, str(run_id)
        )
        if err is not None:
            code, message = err
            group_blocks.append(error_result(call["id"], code, message))
            yield {
                "type": "tool_finished",
                "step": state.step,
                "tool_use_id": call["id"],
                "ok": False,
            }
            continue
        if state.invocations >= _tool_limits().MAX_INVOCATIONS:
            group_blocks.append(error_result(call["id"], "limit", "Limite de invocações."))
            continue
        events, block = await execute_one(
            call=call, rec=rec, approval=approval, ctx=ctx, catalog=catalog,
            run_uuid=str(run_id), owner=user, conversation=conversation, step=state.step,
        )
        for item in events:
            yield _emit(item)
        state.invocations += 1
        group_blocks.append(block)
    if group_blocks:
        state.messages.append({"role": "user", "content": group_blocks})
    state.step += 1
    snap.pop("tool_loop", None)
    # Continua o loop do modelo a partir do estado restaurado.
    meta_model = loop_snap.get("model") or snap.get("requested_model") or ""
    agen = run_tool_events(
        client=client,
        model=meta_model,
        system=loop_snap.get("system") or "",
        max_tokens=int(loop_snap.get("max_tokens") or 1024),
        state=state,
        catalog=catalog,
        ctx=ctx,
        owner=user,
        conversation=conversation,
        run_uuid=str(run_id),
        live=live,
        extra_body=loop_snap.get("extra_body"),
        cancel_flag=lambda: sync_to_async(_cancel_flag)(run_id),
    )
    # Reaproveita o tradutor de eventos terminais do caminho inicial.
    async for ev in _translate_resume(run_id, _emit, agen, snap):
        yield ev


def _tool_limits():
    from chat.services.tools import limits

    return limits


async def _translate_resume(run_id, _emit, agen, snapshot):
    async for ev in agen:
        kind = ev["type"]
        if kind == "turn_paused":
            snapshot["tool_loop"] = {
                "pending": ev["pending"],
                "loop": ev["loop"],
                "model": snapshot.get("requested_model") or "",
                "system": (snapshot.get("tool_loop") or {}).get("system") or "",
                "max_tokens": (snapshot.get("tool_loop") or {}).get("max_tokens") or 1024,
                "extra_body": (snapshot.get("tool_loop") or {}).get("extra_body"),
            }
            text = "".join(ev["loop"]["text_parts"])
            await sync_to_async(_set_paused)(run_id, snapshot, text)
            yield _emit({"type": "run_paused", "state": "awaiting_approval"})
            return
        if kind == "turn_done":
            stopped = ev.get("stopped", "end_turn")
            text = ev.get("final_text", "")
            usage = ev.get("usage") or None
            if stopped == "end_turn":
                await sync_to_async(_finalize)(
                    run_id, state="done", text=text, msg_state="ok",
                    usage=usage, stop_reason=ev.get("stop_reason"),
                )
                yield _emit(
                    {
                        "type": "done",
                        "message_id": await sync_to_async(_assistant_uuid)(run_id),
                        "text": text,
                        "stop_reason": ev.get("stop_reason"),
                        "truncated": False,
                        "model": snapshot.get("requested_model"),
                        "input_tokens": (usage or {}).get("input_tokens"),
                        "output_tokens": (usage or {}).get("output_tokens"),
                    }
                )
            elif stopped == "cancelled":
                await sync_to_async(_finalize)(
                    run_id, state="cancelled", text=text, msg_state="cancelled",
                    error_code="cancelled",
                    error_message="Geração interrompida pelo usuário.",
                )
                yield _emit(
                    {"type": "cancelled", "message": "Geração interrompida pelo usuário."}
                )
            else:
                code = "tool_limit" if stopped == "limit" else "tool_budget"
                message = "Limite do ciclo de ferramentas atingido."
                await sync_to_async(_finalize)(
                    run_id, state="failed", text=text,
                    msg_state="failed" if not text else "partial",
                    error_code=code, error_message=message,
                )
                yield _emit({"type": "error", "code": code, "message": message})
            return
        yield _emit(ev)


def _mark_streaming(run_id: str) -> None:
    from django.utils import timezone

    GenerationRun.objects.filter(uuid=run_id).update(
        state="streaming", last_heartbeat=timezone.now()
    )
