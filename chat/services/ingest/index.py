"""M4: fronteira RetrievalIndex (§4, §14).

`LocalFtsIndex` é o índice oficial do perfil local-lite (FTS5 existente).
`ElasticIndex` é projeção reconstruível do homelab: sem pacote/URL, falha
com mensagem útil em vez de fingir consulta (matriz de degradação).
Perda do índice nunca apaga histórico/documentos: reindexa do banco.
"""

from __future__ import annotations

import os


class RetrievalIndex:
    """Contrato mínimo de índice de recuperação."""

    name = "unknown"

    def index_chunks(self, rows: list[tuple[int, str]]) -> int:
        raise NotImplementedError

    def search(
        self, query: str, *, chunk_ids: list[int], limit: int = 30
    ) -> list[tuple[int, float]]:
        raise NotImplementedError


class LocalFtsIndex(RetrievalIndex):
    name = "local-fts"

    def index_chunks(self, rows: list[tuple[int, str]]) -> int:
        from chat.services.rag import fts

        fts.index_chunks(rows)
        return len(rows)

    def search(
        self, query: str, *, chunk_ids: list[int], limit: int = 30
    ) -> list[tuple[int, float]]:
        from chat.services.rag import fts

        return fts.lexical_search(chunk_ids, query, limit=limit)


class ElasticIndex(RetrievalIndex):
    name = "elastic"

    def __init__(self, url: str | None = None) -> None:
        self.url = url or os.environ.get("ELASTIC_URL", "")
        if not self.url:
            raise RuntimeError(
                "Elastic indisponível: defina ELASTIC_URL (perfil homelab). "
                "Sem índice, o RAG responde falha explícita — nunca finge consulta."
            )

    def _client(self):
        try:
            import elasticsearch  # type: ignore
        except ImportError as exc:
            raise ModuleNotFoundError(
                "Pacote 'elasticsearch' não instalado; índice Elastic indisponível neste perfil."
            ) from exc
        return elasticsearch.Elasticsearch(self.url)

    def index_chunks(self, rows: list[tuple[int, str]]) -> int:
        raise RuntimeError("Indexação Elastic exige o cluster do homelab (preflight M6).")

    def search(
        self, query: str, *, chunk_ids: list[int], limit: int = 30
    ) -> list[tuple[int, float]]:
        raise RuntimeError("Busca Elastic exige o cluster do homelab (preflight M6).")


def select_index(kind: str, **kwargs) -> RetrievalIndex:
    if kind == "local":
        return LocalFtsIndex()
    if kind == "elastic":
        try:
            import elasticsearch  # noqa: F401  # type: ignore
        except ImportError as exc:
            raise ModuleNotFoundError(
                "Pacote 'elasticsearch' não instalado; use kind='local'."
            ) from exc
        return ElasticIndex(**kwargs)
    raise ValueError(f"índice desconhecido: {kind}")
