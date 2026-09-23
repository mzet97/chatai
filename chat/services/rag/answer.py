"""Resposta com evidências — modo Sempre consultar (RAG-06/07/08, M4).

Sem evidência suficiente: devolve abstenção SEM chamada paga.
Com evidências: blocos `search_result` nativos + texto, dentro do orçamento
RAG (até ~3000 tokens Claude ≈ 12000 chars; aproximação registrada, pois
tokens E5 ≠ tokens Claude — a contagem exata global continua em
context_builder via count_tokens).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from chat.models_rag import ConversationKnowledge, Document
from chat.services.rag import retrieval
from chat.services.rag.followup import build_query

RAG_BUDGET_CHARS = 12000
ABSTAIN_NO_EVIDENCE = "Não encontrei evidências suficientes nas fontes selecionadas."

REASONS = {
    "empty": "As fontes selecionadas ainda não têm documentos publicados.",
    "processing": "Os documentos selecionados ainda estão sendo indexados. Aguarde a conclusão.",
    "no_evidence": ABSTAIN_NO_EVIDENCE,
    "technical": "A busca nas fontes falhou por erro técnico. Tente de novo.",
}

RAG_SYSTEM_ADDENDUM = (
    "Você recebeu trechos de documentos (search_result) como ÚNICAS fontes "
    "para afirmações sobre eles. Responda somente o que tem apoio nos trechos; "
    "aponte lacunas e conflitos entre fontes com suas referências [n]. "
    "Sem apoio, diga que não encontrou evidências. Conhecimento geral pode "
    "aparecer separado, sem selo documental. Nunca invente página ou fonte."
)


@dataclass
class RagPrep:
    status: str  # skipped | ready | abstain
    reason: str = ""  # empty|processing|no_evidence|technical (se abstain)
    message: str = ""
    evidences: list = field(default_factory=list)
    effective_query: str = ""
    diagnosis: dict = field(default_factory=dict)
    run_id: int | None = None
    sent_chars: int = 0
    dropped: int = 0


def _scope_states(user_id: int, base_uuids: list[str]) -> tuple[int, int]:
    """(docs prontos, docs pendentes) no escopo — sem vazar títulos."""
    ready = Document.objects.filter(
        base__owner_id=user_id, base__uuid__in=base_uuids, state__in=("ready", "partial")
    ).count()
    pending = Document.objects.filter(
        base__owner_id=user_id,
        base__uuid__in=base_uuids,
    ).exclude(state__in=("ready", "partial", "failed")).count()
    return ready, pending


def prepare(user_id: int, conversation, question: str) -> RagPrep:
    """Recuperação prévia autorizada. Nunca chama a Anthropic."""
    try:
        sel = ConversationKnowledge.objects.get(conversation=conversation)
    except ConversationKnowledge.DoesNotExist:
        return RagPrep(status="skipped")
    base_uuids = [u for u in (sel.bases or []) if u]
    if not base_uuids or sel.mode != "always":
        return RagPrep(status="skipped")
    ready, pending = _scope_states(user_id, base_uuids)
    if ready == 0 and pending > 0:
        return RagPrep(status="abstain", reason="processing",
                       message=REASONS["processing"])
    if ready == 0:
        return RagPrep(status="abstain", reason="empty", message=REASONS["empty"])
    last_q = (
        conversation.messages.filter(role="user").order_by("-seq")
        .values_list("text", flat=True).first()
    )
    bq = build_query(question, last_q)
    try:
        res = retrieval.retrieve(
            user_id, base_uuids, bq.original, effective_query=bq.effective
        )
    except retrieval.ProfileMismatch as exc:
        return RagPrep(status="abstain", reason="technical", message=str(exc))
    except Exception:
        return RagPrep(status="abstain", reason="technical",
                       message=REASONS["technical"])
    if not res.evidences:
        return RagPrep(
            status="abstain", reason="no_evidence", message=REASONS["no_evidence"],
            effective_query=res.effective_query, diagnosis=res.diagnosis,
        )
    # Orçamento: corta menos relevantes, registra o enviado.
    kept, used, dropped = [], 0, 0
    for h in res.evidences:
        if used + len(h.text) > RAG_BUDGET_CHARS and kept:
            dropped += 1
            continue
        kept.append(h)
        used += len(h.text)
    if not kept:  # nem a mínima coube: interrompe com orientação.
        return RagPrep(
            status="abstain", reason="technical",
            message="As evidências excedem o orçamento de contexto. Reduza as fontes selecionadas.",
            effective_query=res.effective_query, diagnosis=res.diagnosis,
        )
    return RagPrep(
        status="ready", evidences=kept, effective_query=res.effective_query,
        diagnosis=res.diagnosis, run_id=res.run_id, sent_chars=used, dropped=dropped,
    )


def build_search_blocks(evidences: list) -> list[dict]:
    """Só os blocos `search_result` (M4: compor junto de imagens sem
    duplicar o texto da pergunta — o texto já abre o conteúdo do usuário)."""
    blocks = []
    for h in evidences:
        blocks.append(
            {
                "type": "search_result",
                "source": h.kb_id,
                "title": f"{h.doc_name} (v{h.version_number})",
                "content": [{"type": "text", "text": h.text}],
            }
        )
    return blocks


def build_user_content(evidences: list, question: str) -> list[dict]:
    """Blocos nativos: search_result por evidência + pergunta uma vez."""
    blocks = build_search_blocks(evidences)
    blocks.append({"type": "text", "text": question})
    return blocks


def _iter_citations(final_message) -> list[dict]:
    """Extrai citações de blocos de texto (SDK real ou simulado)."""
    out = []
    for block in getattr(final_message, "content", None) or []:
        if getattr(block, "type", "") != "text":
            continue
        cites = getattr(block, "citations", None)
        if isinstance(block, dict):
            cites = block.get("citations")
        for c in cites or []:
            if isinstance(c, dict):
                out.append(c)
            else:
                out.append(
                    {
                        "cited_text": getattr(c, "cited_text", ""),
                        "search_result_index": getattr(c, "search_result_index", None),
                    }
                )
    return out


def verify_citations(final_message, evidences: list) -> tuple[list[dict], list[dict]]:
    """Confere citações contra o manifesto da execução.

    Retorna (verificadas, inválidas). Índice do provedor ≠ id do banco:
    resolve por índice de envio ou por casamento exato do trecho (único).
    Referência inválida nunca vira link confiável.
    """
    from chat.services.rag import fts

    verified, invalid = [], []
    for cite in _iter_citations(final_message):
        target = None
        idx = cite.get("search_result_index")
        if isinstance(idx, int) and 0 <= idx < len(evidences):
            target = evidences[idx]
        else:
            cited = fts.fold(cite.get("cited_text", ""))
            # Trecho curto não identifica: exige ≥12 chars para casar.
            matches = (
                [h for h in evidences if cited in fts.fold(h.text)]
                if len(cited) >= 12
                else []
            )
            if len(matches) == 1:
                target = matches[0]
        if target is None:
            invalid.append(cite)
            continue
        verified.append(
            {
                "kb_id": target.kb_id,
                "chunk_id": target.chunk_id,
                "source_index": evidences.index(target) + 1,
                "locator": target.locator,
            }
        )
    # Sem duplicar referências: uma por chunk.
    seen, deduped = set(), []
    for v in verified:
        if v["kb_id"] not in seen:
            seen.add(v["kb_id"])
            deduped.append(v)
    return deduped, invalid


def persist_citations(retrieval_run_id: int, verified: list[dict]) -> list[dict]:
    """Persiste citações verificadas + dependências (para M6). Retorna UI-safe."""
    from chat.models_rag import Chunk, Citation, SourceDependency

    out = []
    for v in verified:
        chunk = Chunk.objects.select_related(
            "version", "version__document", "version__document__base"
        ).get(pk=v["chunk_id"])
        cit = Citation.objects.create(
            run_id=retrieval_run_id,
            chunk=chunk,
            kb_id=v["kb_id"],
            source_index=v["source_index"],
            locator_snapshot=v["locator"],
        )
        SourceDependency.objects.create(
            citation=cit,
            chunk=chunk,
            base_uuid=str(chunk.version.document.base.uuid),
            version_number=chunk.version.number,
        )
        out.append(
            {
                "index": v["source_index"],
                "kb_id": v["kb_id"],
                "doc": chunk.version.document.name,
                "base": chunk.version.document.base.name,
                "version": chunk.version.number,
                "locator": v["locator"],
                "excerpt": chunk.text[:500],
            }
        )
    return out
