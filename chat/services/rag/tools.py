"""Ferramentas locais RAG — modo Via ferramentas (RAG-12, M5).

Somente leitura. Usuário/conversa/bases vêm do ExecutionContext confiável;
a IA nunca escolhe user_id, SQL ou escopo. Respeitam o limite de bytes das
ferramentas via normalize_result do executor.
"""

from __future__ import annotations

from typing import Any

READ_EXCERPT_CHARS = 4000


def _err(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}

_search_schema: dict[str, Any] = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
    "additionalProperties": False,
}

_read_schema: dict[str, Any] = {
    "type": "object",
    "properties": {"evidence_id": {"type": "string"}},
    "required": ["evidence_id"],
    "additionalProperties": False,
}


def _selection_sync(conversation_id: int):
    from chat.models_rag import ConversationKnowledge

    try:
        sel = ConversationKnowledge.objects.select_related("conversation").get(
            conversation_id=conversation_id
        )
    except ConversationKnowledge.DoesNotExist:
        return None
    return sel


async def _search_sync(
    *, user_id: int, conversation_id: int, query: str, run_uuid: str = ""
) -> dict:
    from asgiref.sync import sync_to_async

    from chat.services.rag import retrieval
    from chat.services.rag.followup import build_query

    sel = await sync_to_async(_selection_sync)(conversation_id)
    if sel is None or sel.conversation.owner_id != user_id:
        return _err("no_sources", "Nenhuma fonte selecionada nesta conversa.")
    bases = [u for u in (sel.bases or []) if u]
    if not bases:
        return _err("no_sources", "Nenhuma base selecionada.")
    bq = build_query(query)
    try:
        res = await sync_to_async(retrieval.retrieve)(
            user_id, bases, bq.original, effective_query=bq.effective
        )
    except retrieval.ProfileMismatch as exc:
        return _err("profile_mismatch", str(exc))
    except Exception as exc:
        return _err("retrieval_failed", type(exc).__name__)
    if not res.evidences:
        return {"ok": True, "text": "Sem evidências nas fontes selecionadas."}
    if run_uuid and res.run_id is not None:
        def _stamp():
            from chat.models_rag import RetrievalRun

            RetrievalRun.objects.filter(pk=res.run_id).update(
                generation_run_uuid=run_uuid
            )

        await sync_to_async(_stamp)()
    lines = []
    for i, h in enumerate(res.evidences, start=1):
        lines.append(
            f"[{i}] {h.doc_name} (v{h.version_number}) {h.kb_id}\n{h.text[:1500]}"
        )
    return {"ok": True, "text": "\n\n".join(lines)}


async def search_knowledge_base(args: dict, ctx) -> dict:
    query = args.get("query", "")
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        return _err("invalid_args", "query inválida.")
    return await _search_sync(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        query=query.strip(),
        run_uuid=getattr(ctx, "run_uuid", "") or "",
    )


async def read_knowledge_excerpt(args: dict, ctx) -> dict:
    from asgiref.sync import sync_to_async

    eid = args.get("evidence_id", "")
    if not isinstance(eid, str) or not eid.strip():
        return _err("invalid_args", "evidence_id inválido.")

    def _read():
        from chat.models_rag import Chunk, ConversationKnowledge

        try:
            sel = ConversationKnowledge.objects.get(conversation_id=ctx.conversation_id)
        except ConversationKnowledge.DoesNotExist:
            return None, "not_found"
        if sel.conversation.owner_id != ctx.user_id:
            return None, "not_found"
        try:
            chunk = Chunk.objects.select_related(
                "version", "version__document", "version__document__base"
            ).get(uuid=eid.strip())
        except (Chunk.DoesNotExist, ValueError):
            return None, "not_found"
        doc = chunk.version.document
        base = doc.base
        if doc.owner_id != ctx.user_id or str(base.uuid) not in (sel.bases or []):
            return None, "forbidden"
        if doc.active_version_id != chunk.version_id:
            return None, "stale"  # versão revogada/substituída: releia a busca
        return chunk, None

    chunk, err = await sync_to_async(_read)()
    if err in ("forbidden", "not_found"):
        # Sem seleção, outro dono ou ID estranho: mesmo código, sem oracle.
        return _err("not_found", "Evidência não encontrada.")
    if err == "stale":
        return _err("stale", "Evidência de versão substituída; refaça a busca.")
    text = chunk.text[:READ_EXCERPT_CHARS]
    kb_id = f"kb://{chunk.version.document.base.uuid}/v{chunk.version.number}/{chunk.uuid}"
    return {"ok": True, "text": f"{kb_id}\n{text}"}


def tool_records():
    from chat.services.tools.registry import ToolRecord

    return [
        ToolRecord(
            stable_id="local:search_knowledge_base",
            origin="local",
            original_name="search_knowledge_base",
            description="Busca nas bases selecionadas da conversa; retorna trechos citáveis [n] com kb://.",
            input_schema=_search_schema,
            version="rag1",
            approval="auto",
        ),
        ToolRecord(
            stable_id="local:read_knowledge_excerpt",
            origin="local",
            original_name="read_knowledge_excerpt",
            description="Lê trecho adicional de evidência autorizada (evidence_id).",
            input_schema=_read_schema,
            version="rag1",
            approval="auto",
        ),
    ]


HANDLERS = {
    "local__search_knowledge_base": search_knowledge_base,
    "local__read_knowledge_excerpt": read_knowledge_excerpt,
}
