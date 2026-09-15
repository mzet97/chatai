# RAG local — especificação (requisitos numerados RAG-xxx)

## RAG-01 Bases e isolamento
Bases nomeadas por usuário (`KnowledgeBase`: owner, nome, revisão ativa).
Sem compartilhamento, sem indexação automática de conversas. Seleção por
conversa (`ConversationKnowledge`: bases + modo), persistida no SQLite.

## RAG-02 Upload e formatos
TXT, Markdown, PDF com texto, DOCX. Allowlist por extensão+conteúdo
(magic), 20 MiB/arquivo, 300 págs/PDF, 5.000 frag/doc, 50.000/usuário.
Rejeita executáveis, protegidos não suportados, corrompidos, traversal,
ZIP bomb, relações externas. Originais em diretório privado (não
`MEDIA_URL`). PDF sem texto → `needs_ocr`; parcial → cobertura por página
+ ciência antes de publicar.

## RAG-03 Versões e jobs
`Document` + `DocumentVersion` (hash, texto canônico, localizadores,
extrator, warnings). `rag_worker`: fila SQLite, estados
queued→extracting→chunking→embedding→publishing→ready|cancelled|failed|
needs_ocr; lease/heartbeat, checkpoint, idempotência por (versão, perfil).
Publicação atômica com geração de controle; job cancelado/excluído nunca
publica. Reindex monta provisória; troca ativa é atômica.

## RAG-04 Fragmentação e embeddings
Alvo 320 tokens E5, overlap ≤48, respeita estrutura. Prefixos `query: ` /
`passage: `, limite 512 c/ prefixo, sem truncamento silencioso (subdivide).
`intfloat/multilingual-e5-small` (384 dim), `trust_remote_code=False`,
CPU baseline, carga única por processo, download só via `rag_prepare`.
Vetor float32 BLOB (formato: dim uint32 LE + float32 LE), normalizado.
Revisão/modelo distintos nunca misturam; bases com perfis incompatíveis
não combinam na mesma consulta.

## RAG-05 Busca híbrida
`RetrievalService` único: autorização + versão antes de candidatos. FTS5
Unicode (MATCH seguro, sem concatenação), BM25 (menor = melhor); vetorial
exata NumPy só sobre elegíveis (base, owner, publicada, versão,
permissão). 30/pilar, RRF k=60 pesos iguais, desempate determinístico.
Até 6 fragmentos finais, diversificados. Sem % de confiança. Erro
acionável sem FTS5/modelo; lexical isolado só rotulado.

## RAG-06 Modos e contexto
`always` (pré-busca, padrão c/ bases) e `tools` (via `search_knowledge_base`
/ `read_knowledge_excerpt` no loop existente). Orçamento RAG 3.000 tokens
Claude dentro do total. Acompanhamentos: consulta atual + último tópico
do usuário; ≤1 consulta auxiliar determinística.

## RAG-07 Fontes e citações
`search_result` nativo (SDK 1.5.0 já tipa) em `user` (always) / `tool_result`
(tools). Identidade `kb://<base>/<versao>/<fragmento>`. Validação contra o
manifesto da etapa; UI numera [1..n] com links do servidor. Separar
recuperadas × citadas. Sem suporte → incompatibilidade explícita.

## RAG-08 Resposta e abstenção
Sem evidência suficiente: “Não encontrei evidências suficientes nas fontes
selecionadas.” (sem geração paga). Distinguir base vazia / processando /
parcial / falha / negado / sem evidências. Parcial → responde o apoiado +
lacuna. Conflito → versões/localizadores, sem presumir autoridade por data.

## RAG-09 Revogação e exclusão
Reverificar em busca, leitura, contexto, citação, histórico, download,
exportação, cache (escopo+revisão). Exclusão elimina originais, extrações,
vetores, FTS e caches; mantém tombstones. Revogado nunca reenvia; fonte
antiga fica na versão usada, nunca aponta p/ nova silenciosamente.

## RAG-10 Segurança operacional
Arquivos/fragmentos = não confiáveis (injeção não amplia acesso). Evidências
saem p/ Anthropic (classificação antes). Embeddings = sensíveis. Sessão,
CSRF, quotas, storage privado, sanitização. Sem modelo/vetores/segredos no
Git. Transações curtas; nada pesado com transação aberta.
