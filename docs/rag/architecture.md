# RAG — arquitetura e ADRs

## Pipeline
Upload autenticado → `media_rag/` privado (fora de static/MEDIA público) →
extração → fragmentação → embeddings locais + FTS5 → publicação atômica.
Consulta: seleção → autorização → híbrida → top-6 → orçamento → Claude +
`search_result` → citações verificadas.

## ADR-RAG-01: vetores em BLOB próprio, não extensão nem serviço
Alternativas: `sqlite-vec` (extensão nativa ANN) e serviço separado
(Qdrant/Chroma/pgvector). Escolha v1: BLOB `float32` + NumPy exato.
Motivo: zero dependência nativa extra, exatidão auditável, corpus local
pequeno; top-k incremental só sobre elegíveis. Interface `VectorStore`
pequena permite trocar após benchmark. Não implementar 3 backends.

## ADR-RAG-02: embeddings locais ST, CPU baseline
`intfloat/multilingual-e5-small` (384 dim, 512 tok) via Sentence
Transformers, `trust_remote_code=False`. CPU = baseline obrigatório;
MPS só após medição (não prometer NPU). Carga única por processo
(`rag_embeddings.loader`), fora do event loop (executor limitado).
Download explícito via `rag_prepare` (licença Apache-2.0, origem HF);
depois, tudo offline.

## ADR-RAG-03: worker em comando, fila no SQLite
`rag_worker`: aquisição atômica (`claimed_by`, `lease`), heartbeat,
checkpoint por fase, idempotência por (document_version, profile).
Sem thread de view, sem Redis/Celery. Concorrência 1 (v1).

## ADR-RAG-04: FTS5 espelho, não fonte
Tabela `rag_chunk_fts` (FTS5, unicode61) espelha fragmentos publicados;
sincronizada em publicação/exclusão; `rag_index_check` verifica e
reconstrói. MATCH construído por tokenizer seguro (sem concatenar input
cru).

## Módulos
`chat/services/rag/{models→models_rag, storage, extract, chunk, profiles,
embeddings, loader, vectorstore, fts, worker, retrieval, context_rag,
citations, tools_rag, eval_rag}.py` + `management/commands/rag_{prepare,
worker,index_check,eval}` + views `api_rag.py` + UI Conhecimento/Fontes.
