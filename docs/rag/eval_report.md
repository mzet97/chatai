# Relatório de avaliação de recuperação (§18)

Modelo: intfloat/multilingual-e5-small@614241f622f5 (dim 384), CPU. Corpus: 8 docs PT/EN, 40 perguntas (12 factuais, 8 paráfrases, 4 cross-lang, 4 multi, 4 follow-up, 8 sem resposta). k=6.

| método | Hit@6 | Recall@6 | MRR | cobertura multi | ms médio/p50/p95 |
|---|---|---|---|---|---|
| lexical | 0.031 | 0.031 | 0.031 | 0.0 | 1.0/1.0/1.2 |
| vector | 1.0 | 0.984 | 0.984 | 0.75 | 7.3/7.3/7.7 |
| hybrid | 1.0 | 0.984 | 0.984 | 0.75 | 7.4/7.4/7.8 |

Sem resposta com candidatos: 8/8 (M4 deve abster-se nesses casos).

Falhas híbridas (sem acerto): nenhuma.

Falhas observadas: neste corpus pequeno, a híbrida não supera o baseline vetorial (AND lexical raramente casa perguntas com chunks curtos); o pilar lexical cobre casos que o vetorial pode perder (identificadores, termos raros). Cobertura multi 0,75: top-6 nem sempre contém todas as fontes necessárias.

Meta inicial Hit@6 ≥ 0,85 nas respondíveis; resultado acima, sem ajuste de gabarito.
