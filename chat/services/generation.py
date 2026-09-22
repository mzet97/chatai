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


def content_hash(text: str, images: list[dict] | None = None) -> str:
    """Hash p/ idempotência: texto + carga canônica das imagens (vazio = legado)."""
    h = hashlib.sha256(text.encode("utf-8"))
    for item in images or []:
        h.update(b"\x00img\x00")
        h.update((item.get("media_type") or "").encode("utf-8"))
        h.update(b"\x00")
        h.update((item.get("data") or "").encode("ascii", "ignore"))
    return h.hexdigest()


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
    images: list[dict] | None = None,
) -> tuple[GenerationRun, bool, str | None]:
    """Reserva run `preparing` e adquire exclusividade (transação curta).

    Sem `user_message`: persiste a mensagem do usuário (envio novo).
    Com `user_message`: retry reutiliza a pergunta original, sem duplicá-la.
    `images`: itens canônicos de `images.validate_message_images` (revalidados
    na view); persistidos em `Message.blocks` (texto + imagens).
    Retorna (run, created, None). `created=False` = replay idempotente.
    Levanta `RunBusy` se outra execução ativa; `IdempotencyConflict` em hash distinto.
    """
    from chat.services import images as _images

    images = list(images or [])
    digest = content_hash(content, images)
    existing = GenerationRun.objects.filter(
        conversation=conversation, idempotency_key=idempotency_key
    ).first()
    if existing is not None:
        if existing.content_hash != digest:
            raise IdempotencyConflict("Mesma chave com conteúdo diferente.")
        return existing, False, None
    # Contenção SQLite ("database is locked") recebe retry curto e limitado;
    # esgotado o retry, vira RunBusy (erro recuperável, nunca trava a conversa).
    last_locked: OperationalError | None = None
    msg = user_message  # retry explícito reutiliza; envio novo cria (e descarta em rollback)
    if user_message is None and images:
        blocks = [{"type": "text", "text": content}]
        blocks += [_images.stored_image_block(item) for item in images]
    else:
        blocks = None  # retry reutiliza blocos; texto puro mantém legado []
    for _ in range(6):
        try:
            with transaction.atomic():
                if msg is None:
                    seq = (Message.objects.filter(conversation=conversation).count()) + 1
                    msg = Message.objects.create(
                        conversation=conversation,
                        seq=seq,
                        role="user",
                        text=content,
                        blocks=blocks or [],
                        state="ok",
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
                    content_hash=digest,
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


async def _iter_stream_text(stream):
    """Texto como str, citations_delta do SDK como tupla ("citation", {...}).

    Streams simulados (sem __aiter__): usa text_stream, preservando gating.
    SDK real: itera eventos brutos e extrai text_delta + citations_delta.
    A mensagem final persistida (get_final_message) é a referência canônica.
    """
    if getattr(stream, "__aiter__", None) is None:
        async for delta in stream.text_stream:
            yield delta
        return
    async for event in stream:
        if getattr(event, "type", "") != "content_block_delta":
            continue
        delta = getattr(event, "delta", None)
        dtype = getattr(delta, "type", "")
        if dtype == "text_delta":
            yield getattr(delta, "text", "")
        elif dtype == "citations_delta":
            citation = getattr(delta, "citation", None)
            yield (
                "citation",
                {
                    "cited_text": (getattr(citation, "cited_text", "") or "")[:300],
                },
            )


def _current_images(user_message) -> list[dict]:
    """Imagens da mensagem atual a partir dos blocos persistidos.

    Reencontro verificado (M4): a variante imutável é reverificada aqui;
    divergência levanta StoredImageRevoked (fail closed).
    """
    from chat.services import images as _images

    return _images.verified_image_entries(user_message.blocks or [])


def _history_for(conversation_id: int, upto_seq: int) -> list[dict]:
    from chat.services import images as _images

    rows = (
        Message.objects.filter(conversation_id=conversation_id, seq__lt=upto_seq)
        .order_by("seq")
        .values("seq", "role", "text", "blocks", "state")
    )
    history = []
    for row in rows:
        item = dict(row)
        item["image_blocks"] = _images.verified_image_entries(row.get("blocks"))
        history.append(item)
    return history


def _resolve_snapshot_config(user, conversation) -> tuple[ResolvedConfig, cfg.Credential, str]:
    from chat.services.anthropic_client import resolve_for_user

    resolved, cred = resolve_for_user(user, conversation)
    return resolved, cred, cred.secret or ""


def _resolve_agent_config(conversation) -> dict | None:
    """Agente efetivo como dados puros (seguro p/ atravessar sync_to_async).

    None = caminho Chat inalterado. Com agente: nome do perfil, revisão
    pinada, valores efetivos e origem por campo (AG-1.3/C-A2).
    """
    from chat.services.agents import effective as _effective

    resolved = _effective.resolve_agent(conversation)
    if resolved is None:
        return None
    return {
        "mode": resolved["mode"],
        "definition_name": resolved["definition"].name,
        "version_revision": resolved["version"].revision,
        "values": resolved["values"],
        "origins": resolved["origins"],
    }


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
    from chat.services import thinking as _thinking
    from chat.services import variation as _variation
    from chat.services.base_policy import POLICY_VERSION, compose_system
    from chat.services.providers import get_provider as _provider

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
    # Agente individual (M1/AG-1): perfil publicado pinado sobre o runtime
    # existente. Sem agente (modo chat): tudo abaixo preserva o caminho atual.
    agent = await sync_to_async(_resolve_agent_config)(conversation)
    if agent is None:
        snapshot["agent"] = {"mode": conversation.agent_mode or "chat", "applied": False}
    else:
        snapshot["agent"] = {
            "mode": agent["mode"],
            "applied": True,
            "definition": agent["definition_name"],
            "version_revision": agent["version_revision"],
            "origins": agent["origins"],
            "team_delegation": agent["values"]["team_delegation"],
        }
        instructions = agent["values"]["task_instructions"]
        if instructions:
            composed_system = (
                composed_system
                + "\n\n## Perfil do agente "
                + agent["definition_name"]
                + " (revisão "
                + str(agent["version_revision"])
                + ")\n"
                + instructions
            )
    # Precedência do modelo (C-A2): conversa → versão do agente → padrões.
    # `resolved` já reflete conversa→UI→env→padrão; a versão entra quando a
    # conversa não fixou modelo (origem diferente de "conversation").
    model_name = resolved.model
    if agent is not None and resolved.model_origin != "conversation" and agent["values"]["model"]:
        model_name = agent["values"]["model"]
        snapshot["model"] = model_name
        snapshot["model_origin"] = "agent_version"
    if agent is not None:
        level = agent["values"]["variation_level"]
        think_mode = agent["values"]["thinking_mode"]
        think_level = agent["values"]["thinking_level"]
        think_budget = agent["values"]["thinking_budget"]
    else:
        level = conversation.temperature_level or _variation.DEFAULT_LEVEL
        think_mode = conversation.thinking_mode or _thinking.DEFAULT_MODE
        think_level = conversation.thinking_level or _thinking.DEFAULT_LEVEL
        think_budget = conversation.thinking_budget or _thinking.DEFAULT_BUDGET
    try:
        temp_value, temp_state, temp_reason = _variation.resolve_temperature(
            level, model=model_name, base_url=resolved.base_url
        )
    except ValueError:
        # Dado legado fora do enum: cai para o padrão em vez de quebrar a geração.
        level = _variation.DEFAULT_LEVEL
        temp_value, temp_state, temp_reason = _variation.resolve_temperature(
            level, model=model_name, base_url=resolved.base_url
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
        client = _provider().build_client(
            api_key=secret,
            base_url=base_url,
            timeout_seconds=resolved.timeout_seconds,
            max_retries=resolved.max_retries,
        )

    from chat.services import images as _images
    from chat.services import protocol as _protocol

    try:
        history = await sync_to_async(_history_for)(conversation.id, run.user_message.seq)
        user_images = await sync_to_async(_current_images)(run.user_message)
    except _images.StoredImageRevoked as exc:
        # Anexo revogado/alterado (M4/TV-5.4): nunca serializa para o
        # provedor; tombstone sem bytes, antes de qualquer chamada paga.
        snapshot["images"] = {"count": None, "revoked": True}
        await sync_to_async(_finalize)(
            run_id,
            state="failed",
            msg_state="failed",
            snapshot=snapshot,
            error_code="attachment_revoked",
            error_message=str(exc),
        )
        yield _emit({"type": "error", "code": "attachment_revoked", "message": str(exc)})
        return
    # RAG M4 (modo Sempre consultar): recuperação prévia autorizada.
    # Abstenção finaliza localmente, sem chamada paga.
    from chat.services.rag import answer as _rag_answer

    rag = await sync_to_async(_rag_answer.prepare)(user.id, conversation, run.user_message.text)
    rag_blocks = None
    if rag.status == "abstain":
        await sync_to_async(_answer_abstention)(
            run_id, snapshot, conversation, run, rag.message, rag
        )
        yield _emit(
            {
                "type": "run_started",
                "conversation_id": str(conversation.uuid),
                "message_id": str(run.user_message.uuid),
                "model": model_name,
                "mode": live_mode,
                "requested_mode": requested_mode,
                "rag": {"status": "abstain", "reason": rag.reason},
            }
        )
        yield _emit(
            {
                "type": "done",
                "message_id": await sync_to_async(_assistant_uuid)(run_id),
                "text": rag.message,
                "stop_reason": None,
                "truncated": False,
                "model": model_name,
                "rag": {"status": "abstain", "reason": rag.reason},
            }
        )
        return
    if rag.status == "ready":
        rag_blocks = _rag_answer.build_user_content(rag.evidences, run.user_message.text)
        composed_system = composed_system + "\n\n" + _rag_answer.RAG_SYSTEM_ADDENDUM
        snapshot["rag"] = {
            "status": "ready",
            "retrieval_run_id": rag.run_id,
            "effective_query": rag.effective_query,
            "evidences": len(rag.evidences),
            "sent_chars": rag.sent_chars,
            "dropped": rag.dropped,
            "diagnosis": rag.diagnosis,
        }
    if user_images:
        # RAG textual junto das imagens (M4/TV-5.1): texto 1x, imagens,
        # evidências search_result — sem o texto duplicado do caminho legado.
        search_blocks = (
            _rag_answer.build_search_blocks(rag.evidences) if rag.status == "ready" else None
        )
        current_blocks = _images.build_user_content(
            run.user_message.text, user_images, extra_blocks=search_blocks
        )
    else:
        # Sem imagens: caminho legado intacto (ordem search_result → texto do RAG).
        current_blocks = rag_blocks
    if isinstance(current_blocks, str):
        current_blocks = None  # texto puro: caminho inalterado
    vision = _thinking.vision_for(model=model_name, base_url=resolved.base_url)
    snapshot["images"] = {
        "count": len(user_images),
        "vision": vision,
        "variants": _protocol.image_variant_ids(user_images),
    }
    history_has_images = any(h.get("image_blocks") for h in history)
    if (user_images or history_has_images) and vision == "no":
        # Modelo sem visão (TV-4.4/C-6): bloqueio educado com oferta, sem
        # descarte ou troca silenciosa, antes de qualquer chamada paga.
        message = (
            "Este modelo não processa imagens nesta configuração. "
            "Troque o modelo da conversa ou remova os anexos e envie de novo."
        )
        await sync_to_async(_finalize)(
            run_id,
            state="failed",
            msg_state="failed",
            snapshot=snapshot,
            error_code="vision_unsupported",
            error_message=message,
        )
        yield _emit({"type": "error", "code": "vision_unsupported", "message": message})
        return
    if (user_images or history_has_images) and vision == "unknown":
        snapshot["images"]["vision_warning"] = (
            "Suporte a visão ainda não confirmado para este modelo."
        )
    try:
        built = await build_context(
            history=history,
            current_text=run.user_message.text,
            system=composed_system,
            model=model_name,
            max_tokens=resolved.max_output_tokens,
            input_budget=resolved.input_budget,
            strict=conversation.strict_mode,
            count_tokens=_bind_counter(client),
            current_blocks=current_blocks,
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
    # Pensamento (TV-1 + M3/AG-4): composição central; conflito bloqueia
    # antes de qualquer chamada paga. Desconhecido nunca envia chaves. Com
    # agente, a resolução passa pelo módulo do agente (valores efetivos +
    # origens); sem agente, o caminho Chat segue inalterado.
    from chat.services.agents import thinking as _agent_thinking

    agent_think = None
    try:
        if agent is None:
            think = _thinking.resolve_thinking(
                think_mode,
                think_level,
                model=built.model,
                base_url=resolved.base_url,
                max_tokens=built.max_tokens,
                budget=think_budget,
                show_summary=bool(conversation.thinking_show_summary),
            )
        else:
            agent_think = _agent_thinking.resolve_for_agent(
                values=agent["values"],
                origins=agent["origins"],
                model=built.model,
                base_url=resolved.base_url,
                max_tokens=built.max_tokens,
                show_summary=bool(conversation.thinking_show_summary),
            )
            think = agent_think["think"]
    except ValueError:
        # Dado legado fora do enum: cai para o padrão em vez de quebrar.
        agent_think = None
        think = _thinking.resolve_thinking(
            _thinking.DEFAULT_MODE,
            _thinking.DEFAULT_LEVEL,
            model=built.model,
            base_url=resolved.base_url,
            max_tokens=built.max_tokens,
        )
    snapshot["requested_thinking_mode"] = think_mode
    snapshot["requested_thinking_level"] = think_level
    snapshot["requested_thinking_budget"] = think_budget
    snapshot["thinking_state"] = think["state"]
    snapshot["thinking_reason"] = think["reason"]
    snapshot["thinking_sent"] = think["thinking"] is not None
    snapshot["thinking_effort_sent"] = (think["output_config"] or {}).get("effort")
    snapshot["think_map_version"] = _thinking.THINK_MAP_VERSION
    if think["conflict"] is not None:
        await sync_to_async(_finalize)(
            run_id,
            state="failed",
            msg_state="failed",
            snapshot=snapshot,
            error_code="thinking_conflict",
            error_message=think["reason"],
        )
        yield _emit(
            {
                "type": "error",
                "code": "thinking_conflict",
                "message": (
                    f"{think['reason']} Aumente o teto de saída ou reduza "
                    "o orçamento de pensamento."
                ),
            }
        )
        return
    think_kwargs = {}
    if think["thinking"] is not None:
        think_kwargs["thinking"] = think["thinking"]
    if think["output_config"] is not None:
        think_kwargs["output_config"] = think["output_config"]
    # Cache M2 (AG-5): plano a partir dos valores efetivos do agente
    # (override da conversa → versão → padrões, já resolvidos em `agent`).
    # Caminho Chat: sem plano, payload e snapshot inalterados.
    from chat.services.agents import cache as _cache

    if agent is None:
        cache_plan = _cache.plan(mode="disabled", ttl="5m")
        snapshot["cache"] = {"applied": False, "reason": "chat_no_agent", **cache_plan}
    else:
        cache_plan = _cache.plan(
            mode=agent["values"]["cache_mode"],
            ttl=agent["values"]["cache_ttl"],
            # system em forma de lista (a única que carrega breakpoint;
            # string/NOT_GIVEN → não marcável, sem padding).
            system=_system_param(built.system),
            messages=built.messages,
            thinking_present=think["thinking"] is not None,
            tools_deterministic=True,  # caminho textual, sem tools no payload
        )
        snapshot["cache"] = {
            "applied": bool(cache_plan["eligible"]),
            "requested_mode": agent["values"]["cache_mode"],
            "requested_ttl": agent["values"]["cache_ttl"],
            "origins": {
                "mode": agent["origins"]["cache_mode"],
                "ttl": agent["origins"]["cache_ttl"],
            },
            **cache_plan,
        }
    # Pensamento ativo impede temperatura: preferência preservada no
    # snapshot, parâmetro incompatível omitido (nunca substituído).
    temp_omitted_by_thinking = bool(think_kwargs and temp_value is not None)
    if temp_omitted_by_thinking:
        temp_value = None
        snapshot["temperature_not_applied"] = "thinking"
    if agent is not None:
        # Pensamento efetivo visível (M3/AG-4.1): valores, origens, display e
        # conflito com temperatura. Caminho Chat: chave ausente (inalterado).
        snapshot["agent_thinking"] = {
            "applied": True,
            "effective": (agent_think or {}).get("effective")
            or {
                "mode": think_mode,
                "level": think_level,
                "budget": think_budget,
                "display": think["display"],
                "show_summary": bool(conversation.thinking_show_summary),
            },
            "origins": (agent_think or {}).get("origins")
            or {
                "mode": agent["origins"].get("thinking_mode", "default"),
                "level": agent["origins"].get("thinking_level", "default"),
                "budget": agent["origins"].get("thinking_budget", "default"),
                "show_summary": "conversation",
            },
            "state": think["state"],
            "reason": think["reason"],
            "sent": think["thinking"] is not None,
            "effort_sent": (think["output_config"] or {}).get("effort"),
            "temperature_omitted": temp_omitted_by_thinking,
        }
    # Catálogo congelado por execução: interseção prefs ∩ disponível agora.
    # Vazio = caminho textual inalterado, sem MCP, sem payload de tools.
    from chat.services.tools import catalog as _tool_catalog

    tool_catalog = await sync_to_async(_tool_catalog.authorized_catalog)(conversation)
    if agent is not None and agent["values"]["allowed_tools"]:
        # Fontes/ferramentas do perfil (AG-1.2): interseção com o catálogo da
        # conversa. Lista vazia no perfil = sem restrição adicional (herda).
        allow = set(agent["values"]["allowed_tools"])
        tool_catalog = [r for r in tool_catalog if r.stable_id in allow]
        snapshot["agent_tools_restricted"] = True
    # Equipe (M4): ferramenta interna só do coordenador + raiz AgentRun.
    # Chat/Agente: bloco ausente (caminho inalterado).
    team_spec = None
    if agent is not None and agent.get("mode") == "team":
        team_spec = await sync_to_async(_prepare_team)(
            user=user,
            conversation=conversation,
            agent=agent,
            task=run.user_message.text,
            snapshot=snapshot,
        )
        if team_spec is not None:
            from chat.services.agents import team as _team

            tool_catalog = [*tool_catalog, _team.delegate_record()]
    snapshot["tools_enabled"] = [r.stable_id for r in tool_catalog]
    # Contexto protocolar versionado (M4/TV-3.3): compara o prefixo deste
    # turno com o da execução anterior da conversa (só hashes, sem Base64).
    previous = await sync_to_async(_previous_protocol)(conversation.id, run_id)
    snapshot["protocol"] = _protocol.track(
        previous,
        _protocol.build_record(
            system=built.system,
            model=built.model,
            rag_status=(snapshot.get("rag") or {}).get("status", "skipped"),
            rag_run_id=(snapshot.get("rag") or {}).get("retrieval_run_id"),
            tools_enabled=snapshot["tools_enabled"],
            image_entries=user_images,
        ),
    )
    protocol_warning = _protocol.restart_warning(snapshot["protocol"])
    await sync_to_async(_set_streaming)(run_id, snapshot, context_used, model_name)
    yield _emit(
        {
            "type": "run_started",
            "conversation_id": str(conversation.uuid),
            "message_id": str(run.user_message.uuid),
            "model": model_name,
            "mode": live_mode,
            "requested_mode": requested_mode,
            "rag": snapshot.get("rag", {"status": "skipped"}),
            "protocol": {
                "generation": snapshot["protocol"]["generation"],
                "prefix_changed": snapshot["protocol"]["prefix_changed"],
                "warning": protocol_warning,
            },
        }
    )

    if tool_catalog:
        extra_body = {"temperature": temp_value} if temp_value is not None else None
        delegate = None
        if team_spec is not None:
            delegate = await sync_to_async(_team_hook)(
                spec=team_spec,
                user=user,
                conversation=conversation,
                client=client,
                model=built.model,
                system=built.system,
                max_tokens=built.max_tokens,
                parent_catalog=tool_catalog,
                vision=vision,
                run_id=run_id,
            )
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
            thinking=think_kwargs.get("thinking"),
            output_config=think_kwargs.get("output_config"),
            live=live,
            catalog=tool_catalog,
            snapshot=snapshot,
            vision=vision,
            delegate=delegate,
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
    stream_kwargs.update(think_kwargs)
    if agent is not None:
        # Só o caminho com agente aplica marcadores; Chat segue idêntico.
        stream_kwargs = _cache.apply_to_payload(stream_kwargs, cache_plan)
    try:
        async with client.messages.stream(**stream_kwargs) as stream:
            async for event in _iter_stream_text(stream):
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
                if isinstance(event, tuple):  # (citations_delta) provisório
                    if live:
                        yield _emit({"type": "citation_delta", "citation": event[1]})
                    continue
                delta = event
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
        try:
            await sync_to_async(_finalize)(
                run_id,
                state="failed",
                text=full_text,
                msg_state="failed",
                error_code="persist_failed",
                error_message=f"Resposta recebida mas persistência falhou: {exc}",
            )
        except Exception as exc2:
            yield _emit(
                {
                    "type": "error",
                    "code": "persist_failed",
                    "message": f"Falha dupla ({exc2}); run {run_id} requer reconciliação.",
                }
            )
            return
        yield _emit(
            {
                "type": "error",
                "code": "persist_failed",
                "message": "Resposta recebida, mas a persistência final falhou.",
            }
        )
        return
    snapshot["cache"]["usage"] = {
        "input_tokens": (usage or {}).get("input_tokens"),
        "cache_creation_input_tokens": (usage or {}).get("cache_creation_input_tokens"),
        "cache_read_input_tokens": (usage or {}).get("cache_read_input_tokens"),
        "cache_creation_detail": (usage or {}).get("cache_creation_detail"),
    }
    await sync_to_async(_merge_snapshot)(run_id, snapshot)
    if usage and (usage.get("input_tokens") is not None or usage.get("output_tokens") is not None):
        yield _emit(
            {
                "type": "usage",
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
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
            "cache_creation_input_tokens": (usage or {}).get("cache_creation_input_tokens"),
            "cache_read_input_tokens": (usage or {}).get("cache_read_input_tokens"),
        }
    )
    # RAG M4: citações verificadas contra o manifesto da execução.
    if rag.status == "ready" and rag.run_id is not None:
        verified, invalid = _rag_answer.verify_citations(final, rag.evidences)
        sources = await sync_to_async(_rag_answer.persist_citations)(rag.run_id, verified)
        if invalid:
            snapshot.setdefault("rag", {})["invalid_citations"] = len(invalid)
            await sync_to_async(_merge_snapshot)(run_id, snapshot)
        if sources:
            yield _emit({"type": "sources", "sources": sources})


def _parse_final(final) -> tuple[str, list, str | None, dict, str | None, str | None]:
    """Extrai texto de TODOS os blocos de texto (sem assumir content[0])."""
    texts, blocks = [], []
    for block in getattr(final, "content", None) or []:
        if getattr(block, "type", "") == "text":
            texts.append(getattr(block, "text", ""))
            blocks.append({"type": "text", "text": getattr(block, "text", "")})
    usage_obj = getattr(final, "usage", None)
    if not usage_obj:
        usage = {}
    else:
        # Métricas de cache (AG-5.2): só o confirmado em Usage; ausente =
        # desconhecido (None), nunca zero presumido.
        from chat.services.agents import cache as _cache

        usage = {
            "input_tokens": getattr(usage_obj, "input_tokens", None),
            "output_tokens": getattr(usage_obj, "output_tokens", None),
            **{k: v for k, v in _cache.parse_usage(usage_obj).items() if k.startswith("cache_")},
        }
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


def _answer_abstention(run_id: str, snapshot: dict, conversation, run, text: str, rag) -> None:
    """Persiste abstenção RAG como resposta local: sem chamada ao provedor."""
    with transaction.atomic():
        db_run = GenerationRun.objects.get(uuid=run_id)
        seq = Message.objects.filter(conversation_id=db_run.conversation_id).count() + 1
        answer = Message.objects.create(
            conversation_id=db_run.conversation_id,
            seq=seq,
            role="assistant",
            text=text,
            state="ok",
        )
        db_run.assistant_message = answer
        db_run.save()
    snapshot["rag"] = {"status": "abstain", "reason": rag.reason}
    _finalize(run_id, state="done", text=text, msg_state="ok", snapshot=snapshot)


def _merge_snapshot(run_id: str, snapshot: dict) -> None:
    GenerationRun.objects.filter(uuid=run_id).update(snapshot=snapshot)


def _verify_tool_citations(run_id: str, user_id: int, final_blocks: list) -> list[dict]:
    """Verifica citações do tool path contra runs RAG vinculadas à geração."""
    from types import SimpleNamespace

    from chat.models_rag import Evidence, RetrievalRun
    from chat.services.rag import answer as _rag_answer

    runs = RetrievalRun.objects.filter(owner_id=user_id, generation_run_uuid=str(run_id)).order_by(
        "id"
    )
    if not runs.exists():
        return []
    evidences = []
    for ev in (
        Evidence.objects.filter(run__in=runs).select_related("chunk").order_by("run_id", "order")
    ):
        c = ev.chunk
        evidences.append(_to_hit(ev, c))
    if not evidences:
        return []
    fake_final = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="", citations=b.get("citations"))
            for b in final_blocks
            if b.get("type") == "text" and b.get("citations")
        ]
    )
    verified, _ = _rag_answer.verify_citations(fake_final, evidences)
    if not verified:
        return []
    first_run = runs.first()
    return _rag_answer.persist_citations(first_run.pk, verified)


def _to_hit(ev, chunk):
    from chat.services.rag.retrieval import EvidenceHit

    doc = chunk.version.document
    base = doc.base
    return EvidenceHit(
        chunk_id=chunk.pk,
        chunk_uuid=str(chunk.uuid),
        base_uuid=str(base.uuid),
        base_name=base.name,
        doc_name=doc.name,
        version_number=chunk.version.number,
        text=chunk.text,
        locator=chunk.locator,
        rrf_score=ev.rrf_score,
        rank_lexical=ev.rank_lexical,
        rank_vector=ev.rank_vector,
    )


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
        state__in=("queued", "running", "preparing", "streaming", "awaiting_approval"),
    ).update(cancel_requested=True)
    if n:
        _cancel_team_tree(run_id)
    return n > 0


def _cancel_team_tree(run_id: str) -> int:
    """Cancelamento estruturado (M4): fecha a árvore AgentRun da raiz.

    Nunca levanta (o flag cooperativo acima já vale); retorna nº fechado.
    """
    try:
        run = GenerationRun.objects.filter(uuid=run_id).first()
        team = (run.snapshot or {}).get("team") if run else None
        root_uuid = (team or {}).get("parent_run_uuid")
        if not root_uuid:
            return 0
        from chat.models_agents import AgentRun
        from chat.services.agents import budget as _budget

        root = AgentRun.objects.filter(uuid=root_uuid).first()
        if root is None:
            return 0
        return _budget.cancel_tree(root, reason="cancelled")
    except Exception:
        return 0


def _set_paused(run_id: str, snapshot: dict, text: str) -> None:
    """Pausa humana: estado persistido, sem transação aberta, sem espera em memória."""
    with transaction.atomic():
        run = GenerationRun.objects.get(uuid=run_id)
        run.state = "awaiting_approval"
        run.snapshot = snapshot
        run.save(update_fields=["state", "snapshot"])
        if run.assistant_message_id:
            Message.objects.filter(pk=run.assistant_message_id).update(text=text, state="partial")


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


def _previous_protocol(conversation_id: int, exclude_run_id: str) -> dict | None:
    """Registro protocolar da execução anterior (para detectar prefixo novo)."""
    from chat.services import protocol as _protocol

    prev = (
        GenerationRun.objects.filter(conversation_id=conversation_id)
        .exclude(uuid=exclude_run_id)
        .order_by("-started_at")
        .values_list("snapshot", flat=True)
        .first()
    )
    return _protocol.previous_record(prev)


def _prepare_team(*, user, conversation, agent, task, snapshot) -> dict | None:
    """Raiz AgentRun + bloco `snapshot["team"]`. Sync (via sync_to_async).

    Só modo Equipe com coordenador delegável cria raiz/hook; sem
    delegáveis, registra indisponível sem quebrar a geração textual.
    """
    from chat.models_agents import AgentDefinition, AgentRun
    from chat.services.agents import budget as _budget

    try:
        definition = AgentDefinition.objects.get(
            owner_id=conversation.owner_id, name=agent["definition_name"]
        )
        version = definition.versions.get(revision=agent["version_revision"])
    except Exception:
        snapshot["team"] = {"available": False, "reason": "no_version"}
        return None
    delegatable = list(version.delegatable_ids or [])
    if not delegatable:
        snapshot["team"] = {"available": False, "reason": "no_delegatables"}
        return None
    root = AgentRun.objects.create(
        owner_id=conversation.owner_id,
        conversation=conversation,
        agent_version=version,
        depth=0,
        task=(task or "")[:2000],
        state="running",
        checkpoint={"started_at": time.time()},
    )
    _budget.log_event(run=root, kind="team_started", payload={"coordinator": definition.name})
    snapshot["team"] = {
        "available": True,
        "parent_run_uuid": str(root.uuid),
        "coordinator": definition.name,
        "revision": version.revision,
        "delegatable_count": len(delegatable),
        "limits": {
            "max_child_runs": _budget.MAX_CHILD_RUNS,
            "max_depth": _budget.MAX_DEPTH,
            "max_parallel": _budget.MAX_PARALLEL,
            "max_generations": _budget.MAX_GENERATIONS,
            "max_invocations": _budget.MAX_INVOCATIONS,
            "max_output_tokens": _budget.MAX_OUTPUT_TOKENS,
            "active_budget_s": _budget.ACTIVE_BUDGET_S,
        },
    }
    return {"root_id": root.pk, "version_id": version.pk}


def _team_hook(
    *,
    spec,
    user,
    conversation,
    client,
    model,
    system,
    max_tokens,
    parent_catalog,
    vision,
    run_id,
) -> dict | None:
    """Hook `delegate` para o loop de ferramentas. Sync (via sync_to_async)."""
    from chat.models_agents import AgentRun, AgentVersion
    from chat.services.agents import budget as _budget
    from chat.services.agents import team as _team

    try:
        root = AgentRun.objects.get(pk=spec["root_id"])
        version = AgentVersion.objects.select_related("definition").get(pk=spec["version_id"])
    except Exception:
        return None

    def resolve_specialist(uuid_str):
        from chat.models_agents import AgentDefinition
        from chat.services.agents.profiles import is_complete

        try:
            definition = AgentDefinition.objects.get(uuid=uuid_str, owner_id=conversation.owner_id)
        except Exception:
            return None
        for cand in definition.versions.order_by("-revision"):
            if cand.published and is_complete(cand):
                return cand
        return None

    async def child_runner(child_spec):
        from asgiref.sync import sync_to_async as _sta

        out = await _team.default_child_runner(
            child_spec,
            client=client,
            model=model,
            system=system or "",
            max_tokens=max_tokens,
            owner=user,
            conversation=conversation,
            vision=vision,
            cancel_flag=lambda: _sta(_cancel_flag)(run_id),
        )
        stats = child_spec.get("stats") or {}

        def _record():
            try:
                db_root = AgentRun.objects.get(pk=root.pk)
                db_child = AgentRun.objects.get(uuid=child_spec["child_uuid"])
                if stats.get("model_calls"):
                    _budget.record(
                        root_run=db_root,
                        run=db_child,
                        kind="call_slot",
                        tokens=int(stats["model_calls"]),
                    )
                if stats.get("output_tokens") is not None:
                    _budget.record(
                        root_run=db_root,
                        run=db_child,
                        kind="usage",
                        tokens=int(stats["output_tokens"] or 0),
                    )
            except Exception:
                pass

        await _sta(_record)()
        return out

    return _team.build_handler(
        owner=user,
        conversation=conversation,
        parent_run=root,
        coordinator_version=version,
        mode="team",
        parent_catalog=parent_catalog,
        resolve_specialist=resolve_specialist,
        child_runner=child_runner,
    )


def _resume_team_hook(*, user, conversation, snap, client, run_id) -> dict | None:
    """Reconstrói o hook `delegate` na retomada. Sync (via sync_to_async)."""
    from chat.models_agents import AgentRun

    team = snap.get("team") or {}
    if not team.get("available") or not team.get("parent_run_uuid"):
        return None
    try:
        root = AgentRun.objects.get(uuid=team["parent_run_uuid"])
    except Exception:
        return None
    if root.agent_version_id is None:
        return None
    loop_snap = snap.get("tool_loop") or {}
    return _team_hook(
        spec={"root_id": root.pk, "version_id": root.agent_version_id},
        user=user,
        conversation=conversation,
        client=client,
        model=loop_snap.get("model") or snap.get("requested_model") or "",
        system=loop_snap.get("system") or "",
        max_tokens=int(loop_snap.get("max_tokens") or 1024),
        parent_catalog=[],
        vision=(snap.get("images") or {}).get("vision"),
        run_id=run_id,
    )


async def _execute_tool_path(
    run_id,
    _emit,
    *,
    user,
    conversation,
    client,
    model,
    system,
    max_tokens,
    extra_body,
    thinking,
    output_config,
    live,
    catalog,
    snapshot,
    vision=None,
    delegate=None,
):
    """Loop explícito com eventos finos; `done` só no turno inteiro."""
    from chat.services.tools.chat_loop import LoopState, run_tool_events
    from chat.services.tools.context import ExecutionContext

    ctx = ExecutionContext(user_id=user.pk, conversation_id=conversation.pk, run_uuid=str(run_id))
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
        thinking=thinking,
        output_config=output_config,
        cancel_flag=lambda: sync_to_async(_cancel_flag)(run_id),
        vision=vision,
        delegate=delegate,
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
                "thinking": thinking,
                "output_config": output_config,
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
                    run_id,
                    state="done",
                    text=text,
                    msg_state="ok",
                    usage=usage,
                    stop_reason=ev.get("stop_reason"),
                )
                tool_sources = await sync_to_async(_verify_tool_citations)(
                    run_id, user.pk, ev.get("final_blocks") or []
                )
                if tool_sources:
                    yield _emit({"type": "sources", "sources": tool_sources})
                if usage and (
                    usage.get("input_tokens") is not None or usage.get("output_tokens") is not None
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
                    run_id,
                    state="cancelled",
                    text=text,
                    msg_state="cancelled",
                    error_code="cancelled",
                    error_message="Geração interrompida pelo usuário.",
                )
                yield _emit({"type": "cancelled", "message": "Geração interrompida pelo usuário."})
            else:
                code = "tool_limit" if stopped == "limit" else "tool_budget"
                message = (
                    "Limite de etapas de ferramentas atingido."
                    if stopped == "limit"
                    else "Orçamento de tempo do ciclo de ferramentas esgotado."
                )
                await sync_to_async(_finalize)(
                    run_id,
                    state="failed",
                    text=text,
                    msg_state="failed" if not text else "partial",
                    error_code=code,
                    error_message=message,
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
        _save_invocation,
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
            run_id,
            state="cancelled",
            msg_state="cancelled",
            error_code="cancelled",
            error_message="Geração interrompida pelo usuário.",
        )
        yield _emit({"type": "cancelled", "message": "Geração interrompida pelo usuário."})
        return
    snap = run.snapshot or {}
    loop_snap = snap.get("tool_loop") or {}
    pending = loop_snap.get("pending", [])
    state = LoopState.from_snapshot(loop_snap.get("loop", {}))
    catalog = await sync_to_async(_tool_catalog.authorized_catalog)(conversation)
    ctx = ExecutionContext(user_id=user.pk, conversation_id=conversation.pk, run_uuid=str(run_id))
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
        from chat.services.providers import get_provider as _provider

        try:
            base_url = _cfg.validate_base_url(resolved.base_url)
        except ValueError as exc:
            yield _emit({"type": "error", "code": "validation", "message": str(exc)})
            return
        client = _provider().build_client(
            api_key=secret,
            base_url=base_url,
            timeout_seconds=resolved.timeout_seconds,
            max_retries=resolved.max_retries,
        )
    await sync_to_async(_mark_streaming)(run_id)
    yield _emit(
        {"type": "run_started", "resumed": True, "mode": snap.get("effective_response_mode")}
    )
    # Equipe (M4): delegações pausadas junto de aprovações retomam pelo hook
    # (filhas concluídas não repetem: o handler valida slots/estado atual).
    team_hook = await sync_to_async(_resume_team_hook)(
        user=user, conversation=conversation, snap=snap, client=client, run_id=str(run_id)
    )
    if team_hook is not None:
        catalog = [*catalog, team_hook["record"]]
    # Grupo pausado: revalida cada item antes do efeito; sem re-perguntar.
    group_blocks: list[dict] = []
    for entry in pending:
        call = {
            "id": entry["tool_use_id"],
            "name": entry["name"],
            "input": entry.get("args", {}),
        }
        if team_hook is not None and entry.get("stable_id") == team_hook["record"].stable_id:
            events, block = await team_hook["handle"](call)
            for item in events:
                yield _emit(item)
            state.invocations += 1
            group_blocks.append(block)
            continue
        rec, approval, err = await sync_to_async(check_resume_entry)(
            entry, catalog, user, str(run_id)
        )
        if err is not None:
            code, message = err
            group_blocks.append(error_result(call["id"], code, message))
            await sync_to_async(_save_invocation)(
                owner=user,
                conversation=conversation,
                run_uuid=str(run_id),
                step=state.step,
                rec=rec,
                call=call,
                decision="approve" if code not in ("refused",) else "deny",
                state="refused" if code in ("refused",) else "failed",
                ok=False,
                text="",
                error_code=code,
            )
            yield _emit(
                {
                    "type": "tool_finished",
                    "step": state.step,
                    "tool_use_id": call["id"],
                    "ok": False,
                }
            )
            continue
        if state.invocations >= _tool_limits().MAX_INVOCATIONS:
            group_blocks.append(error_result(call["id"], "limit", "Limite de invocações."))
            await sync_to_async(_save_invocation)(
                owner=user,
                conversation=conversation,
                run_uuid=str(run_id),
                step=state.step,
                rec=rec,
                call=call,
                decision="approve",
                state="failed",
                ok=False,
                text="",
                error_code="limit",
            )
            yield _emit(
                {
                    "type": "tool_finished",
                    "step": state.step,
                    "tool_use_id": call["id"],
                    "ok": False,
                }
            )
            continue
        events, block = await execute_one(
            call=call,
            rec=rec,
            approval=approval,
            ctx=ctx,
            catalog=catalog,
            run_uuid=str(run_id),
            owner=user,
            conversation=conversation,
            step=state.step,
            vision=(snap.get("images") or {}).get("vision"),
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
        thinking=loop_snap.get("thinking"),
        output_config=loop_snap.get("output_config"),
        cancel_flag=lambda: sync_to_async(_cancel_flag)(run_id),
        vision=(snap.get("images") or {}).get("vision"),
        delegate=team_hook,
    )
    # Reaproveita o tradutor de eventos terminais do caminho inicial.
    async for ev in _translate_resume(run_id, _emit, agen, snap, user.pk):
        yield ev


def _tool_limits():
    from chat.services.tools import limits

    return limits


async def _translate_resume(run_id, _emit, agen, snapshot, user_id: int):
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
                "thinking": (snapshot.get("tool_loop") or {}).get("thinking"),
                "output_config": (snapshot.get("tool_loop") or {}).get("output_config"),
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
                    run_id,
                    state="done",
                    text=text,
                    msg_state="ok",
                    usage=usage,
                    stop_reason=ev.get("stop_reason"),
                )
                tool_sources = await sync_to_async(_verify_tool_citations)(
                    run_id, user_id, ev.get("final_blocks") or []
                )
                if tool_sources:
                    yield _emit({"type": "sources", "sources": tool_sources})
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
                    run_id,
                    state="cancelled",
                    text=text,
                    msg_state="cancelled",
                    error_code="cancelled",
                    error_message="Geração interrompida pelo usuário.",
                )
                yield _emit({"type": "cancelled", "message": "Geração interrompida pelo usuário."})
            else:
                code = "tool_limit" if stopped == "limit" else "tool_budget"
                message = "Limite do ciclo de ferramentas atingido."
                await sync_to_async(_finalize)(
                    run_id,
                    state="failed",
                    text=text,
                    msg_state="failed" if not text else "partial",
                    error_code=code,
                    error_message=message,
                )
                yield _emit({"type": "error", "code": code, "message": message})
            return
        yield _emit(ev)


def _mark_streaming(run_id: str) -> None:
    from django.utils import timezone

    GenerationRun.objects.filter(uuid=run_id).update(
        state="streaming", last_heartbeat=timezone.now()
    )
