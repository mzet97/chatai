"""M5: revalidação de evidências filho → pai (§6).

Texto do filho é produto de trabalho não confiável, nunca fonte primária.
Só vira citação documental o que resolve para um Chunk autorizado: mesmo
dono e base dentro das fontes selecionadas da conversa (interseção de
permissões; delegação nunca amplia acesso). Todo o resto é descartado
como citação — sem copiar índices entre payloads, sem "o revisor disse".
Puro sobre o banco; nunca levanta por ref malformada.
"""

from __future__ import annotations

import uuid as _uuid

EXCERPT_CHARS = 300
MAX_REFS = 20


def _candidate_uuids(refs: list) -> list[str]:
    out = []
    for ref in refs or []:
        if isinstance(ref, dict) and isinstance(ref.get("chunk_uuid"), str):
            out.append(ref["chunk_uuid"])
        elif isinstance(ref, str):
            try:
                _uuid.UUID(ref)
            except ValueError:
                continue
            out.append(ref)
    return out[:MAX_REFS]


def revalidate(*, owner, conversation, refs: list) -> dict:
    """Resolve refs contra chunks autorizados. Retorna {"valid", "dropped"}.

    `valid`: [{chunk_uuid, doc, locator, excerpt}]. `dropped`: reprs curtas
    do que não resolveu (auditoria, nunca citação).
    """
    from chat.models_rag import Chunk, ConversationKnowledge

    try:
        knowledge = ConversationKnowledge.objects.get(conversation=conversation)
        bases = set(knowledge.bases or [])
    except ConversationKnowledge.DoesNotExist:
        bases = set()
    candidates = _candidate_uuids(refs)
    chunks = {
        str(c.uuid): c
        for c in Chunk.objects.select_related(
            "version", "version__document", "version__document__base"
        ).filter(uuid__in=candidates)
    }
    valid, dropped = [], []
    for raw in candidates:
        chunk = chunks.get(raw)
        doc = getattr(getattr(chunk, "version", None), "document", None)
        base = getattr(doc, "base", None)
        if (
            chunk is None
            or doc is None
            or base is None
            or doc.owner_id != owner.pk
            or str(base.uuid) not in bases
        ):
            dropped.append(raw[:100])
            continue
        valid.append(
            {
                "chunk_uuid": str(chunk.uuid),
                "doc": doc.name[:200],
                "locator": chunk.locator,
                "excerpt": (chunk.text or "")[:EXCERPT_CHARS],
            }
        )
    for ref in refs or []:
        if isinstance(ref, dict) and not isinstance(ref.get("chunk_uuid"), str):
            dropped.append(str(ref)[:100])
        elif isinstance(ref, str):
            try:
                _uuid.UUID(ref)
            except ValueError:
                dropped.append(ref[:100])
    return {"valid": valid, "dropped": dropped[:MAX_REFS]}
