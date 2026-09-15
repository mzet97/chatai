"""Índice lexical FTS5/BM25 (RAG-05, infra M2).

Tabela virtual `rag_chunk_fts(chunk_id UNINDEXED, text)` com tokenizer
unicode61 puro. O build SQLite do projeto rejeita opções do unicode61
(`remove_diacritics`), então a dobra de acentos é feita no aplicativo
(`fold`): índice e consulta usam o mesmo folding, busca insensível a
acentos. Sincronização explícita na publicação (sem triggers): só a
versão ativa de cada documento é indexada. MATCH construído por
tokenização própria — nunca concatena texto do usuário na expressão.
"""

from __future__ import annotations

import re
import unicodedata

from django.db import connection

FTS_TABLE = "rag_chunk_fts"
_WORD = re.compile(r"[\w]+", re.UNICODE)
MAX_TERMS = 32


def fold(text: str) -> str:
    """Remove marcas diacríticas (NFD − Mn). Caixa fica com o unicode61."""
    nfkd = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def ensure_fts() -> None:
    """Cria a tabela virtual se ausente. Erro acionável sem FTS5."""
    try:
        with connection.cursor() as cur:
            cur.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} "
                "USING fts5(chunk_id UNINDEXED, text, tokenize='unicode61')"
            )
    except Exception as exc:
        raise RuntimeError(
            f"FTS5 indisponível neste SQLite ({type(exc).__name__}). "
            "Busca lexical desabilitada; reinstale o Python com FTS5."
        ) from exc


def build_match(query: str) -> str | None:
    """Constrói expressão MATCH segura: termos citados com AND implícito.

    Retorna None quando não há termo pesquisável (só pontuação/stop).
    """
    terms = _WORD.findall(fold(query))
    terms = [t for t in terms if len(t) >= 2][:MAX_TERMS]
    if not terms:
        return None
    # Aspas duplicadas escapam dentro de frase FTS5; sem operadores crus.
    return " ".join('"' + t.replace('"', '""') + '"' for t in terms)


def index_chunks(rows: list[tuple[int, str]]) -> None:
    """Substituição total de um conjunto de chunks (por versão, no publish).

    Indexa o texto com folding de acentos; o original permanece em Chunk.
    """
    ensure_fts()
    with connection.cursor() as cur:
        ids = [r[0] for r in rows]
        if ids:
            placeholders = ",".join(["%s"] * len(ids))
            cur.execute(
                f"DELETE FROM {FTS_TABLE} WHERE chunk_id IN ({placeholders})", ids
            )
        if rows:
            cur.executemany(
                f"INSERT INTO {FTS_TABLE} (chunk_id, text) VALUES (%s, %s)",
                [(cid, fold(text)) for cid, text in rows],
            )


def delete_chunks(chunk_ids: list[int]) -> None:
    if not chunk_ids:
        return
    ensure_fts()
    with connection.cursor() as cur:
        placeholders = ",".join(["%s"] * len(chunk_ids))
        cur.execute(
            f"DELETE FROM {FTS_TABLE} WHERE chunk_id IN ({placeholders})",
            list(chunk_ids),
        )


def lexical_search(
    chunk_ids: list[int], query: str, limit: int = 30
) -> list[tuple[int, float]]:
    """BM25 sobre chunks elegíveis. Retorna [(chunk_id, bm25)] (menor=melhor).

    `chunk_ids` já filtrados por autorização/versão (M3). Lista vazia ou
    MATCH sem termos → [] (diagnóstico lexical rotulado, sem fingir híbrida).
    """
    if not chunk_ids:
        return []
    match = build_match(query)
    if match is None:
        return []
    ensure_fts()
    placeholders = ",".join(["%s"] * len(chunk_ids))
    # MATCH não aceita placeholder de coluna em todas as versões: a
    # expressão é construída por build_match (só termos citados), o valor
    # via parâmetro.
    sql = (
        f"SELECT chunk_id, bm25({FTS_TABLE}) AS rank "
        f"FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH %s "
        f"AND chunk_id IN ({placeholders}) "
        "ORDER BY rank LIMIT %s"
    )
    with connection.cursor() as cur:
        cur.execute(sql, [match, *chunk_ids, limit])
        return [(int(cid), float(rank)) for cid, rank in cur.fetchall()]


def count_indexed(chunk_ids: list[int]) -> int:
    if not chunk_ids:
        return 0
    ensure_fts()
    placeholders = ",".join(["%s"] * len(chunk_ids))
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) FROM {FTS_TABLE} WHERE chunk_id IN ({placeholders})",
            list(chunk_ids),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0
