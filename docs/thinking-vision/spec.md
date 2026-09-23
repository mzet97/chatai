# Thinking + Visão — Especificação SDD

Verificado no ambiente em 15/09/2026 (SDK `anthropic==1.5.0`, Django 5.2, SQLite).
«Desconhecido» = controle desabilitado, nunca presumido.

## TV-1. Pensamento (configuração oficial, só resumo exibido)

- TV-1.1 Controle **Pensamento** por conversa: `default` (padrão do modelo,
  não sobrescreve) | `disabled` (só enviada se o modelo aceitar) | `enabled`
  (adaptativo quando disponível; orçamento manual só se suportado).
- TV-1.2 **Nível de pensamento** Baixo/Médio/Alto, separado do seletor
  **Variação** (temperatura). Novas conversas: modo `default`, nível salvo
  `medium` sem efeito até escolha explícita.
- TV-1.3 Adaptativo resolve nível → `output_config.effort` = `low|medium|high`.
  Sem `xhigh`/`max` nesta versão.
- TV-1.4 Manual usa presets 1.024/2.048/4.096 com `budget_tokens < max_tokens`
  (conservador, sem exceção de interleaving). Conflito (ex. budget 2.048 com
  teto 1.024) bloqueia antes da chamada e oferece total confirmado.
- TV-1.5 **Exibir resumo** separado de ligar: `display="summarized"|"omitted"`
  só com pensamento explicitamente ativado e suporte confirmado. Modo `default`
  nunca liga pensamento só para obter resumo; `disabled` nunca envia `display`.
- TV-1.6 Composição central de argumentos: preferência incompatível é
  preservada, marcada não aplicada e omitida (temperatura, `top_p`/`top_k`,
  tool choice). Aviso UI: "Pode aumentar o tempo de resposta e o consumo
  de tokens." Sem aumento silencioso de limites.
- TV-1.7 Snapshot imutável por execução inclui config solicitada/efetiva de
  pensamento; mesmo turno de ferramentas mantém a config em todas as etapas.
  Mudanças bloqueadas durante geração ou aprovação pendente.

## TV-2. Resumo do pensamento (apresentação)

- TV-2.1 Painel recolhível **Resumo do pensamento** só com conteúdo
  disponibilizado e autorizado; separado de resposta, ferramentas e fontes RAG.
- TV-2.2 Estados distintos: resumo, vazio, ausente, opaco (`redacted_thinking`),
  interrompido. Nada inventado; sem segunda chamada para fabricar resumo;
  `signature`/opacos nunca na UI; cópia da resposta copia só o texto final.
- TV-2.3 "Pensando…" só com evento observado; sem porcentagem/tempo interno.
  Sem autoscroll obrigatório; componentes/temas/acessibilidade existentes.

## TV-3. Streaming e protocolo

- TV-3.1 Eventos estruturados do SDK (nunca só `text_stream`); agregação por
  execução/etapa/índice distinguindo texto, pensamento, assinatura, args de
  ferramenta e citações; mensagem final pelo helper oficial.
- TV-3.2 Eventos da app `thinking_started|thinking_delta|thinking_completed`
  só com projeções liberadas. Protocolar integral e projeção sanitizada
  persistidos separadamente; replay com blocos completos (incl. opacos) e
  `tool_result` na ordem exigida.
- TV-3.3 Registro versionado do contexto protocolar (variantes de imagem +
  evidências). Mudança de prefixo entre turnos → nova versão registrada e
  aviso de reinício do raciocínio; nunca adivinhar protocolo nem conservar
  dado revogado para satisfazer assinatura.

## TV-4. Imagens no compositor

- TV-4.1 **Anexar imagem** ao lado do campo: seletor nativo real,
  arrastar/soltar, colar com ⌘V só quando houver arquivo de imagem (texto
  cola normal). Anexar prepara, não chama a API.
- TV-4.2 Miniaturas com nome escapado, dimensões, tamanho, estado, **Remover**,
  zoom em diálogo, ordem Imagem 1/2/…. Limites: 4 novas/mensagem, 5 MiB e
  20 MP por original, 20 MiB serializados por chamada (incl. histórico).
- TV-4.3 Formatos: JPEG, PNG, WebP, GIF estático. Animação rejeitada com
  explicação (sem usar 1º quadro em silêncio). HEIC/SVG/TIFF fora de escopo.
- TV-4.4 Servidor valida tudo (MIME real, Pillow com limites, truncados,
  decompression bombs). Variante normalizada (EXIF, sem metadados, sem
  ampliação/corte, transparência preservada) + thumbnail separada; a variante
  é imutável e é o que vai ao modelo como bloco `image` Base64 — montado só
  na serialização, nunca estocado em snapshots/logs.
- TV-4.5 Destino **Anexo da conversa**, separado de **Documento da base RAG**.
  Posterior sobre imagem anterior reencontra o anexo após restart, sem
  duplicar. Modelo sem visão → geração bloqueada com oferta (trocar modelo /
  remover anexos), sem descarte ou troca silenciosa.
- TV-4.6 Sem OCR como substituto de visão; sem Files API nesta entrega;
  texto em imagem/QR/nome é dado não confiável (nunca autorização);
  previews/downloads por rotas autenticadas.

## TV-5. Composição e segurança

- TV-5.1 RAG textual + imagens + pensamento + ferramentas numa chamada via
  construtor único de contexto. Imagem não vira documento indexado; Base64
  nunca no encoder textual; sem chamada oculta de descrição/OCR.
- TV-5.2 Resultados de imagem de ferramentas MCP só de ferramentas
  autorizadas com capacidade correspondente; limites binários separados do
  teto de texto (sem truncar Base64 para 32 KiB).
- TV-5.3 Métricas por execução (modelo, config efetiva, tempos até 1º evento/
  1º texto, duração, imagens/bytes, uso da API). Pensamento detalhado é
  subconjunto da saída — nunca somado. Ausente = desconhecido, não zero.
- TV-5.4 Abandonados expiram por rotina sem tocar vinculados; revogação
  invalida payloads/previews/projeções futuras (tombstone não sensível).

## TV-6. Aceite

Suíte com mocks (SDK/transportes), imagens sintéticas fictícias, navegador +
ASGI reais com SQLite em arquivo; upload pela UI de verdade (fixture no banco
não substitui). Grupos da §16 do prompt (config, limites, pensamento,
protocolo, prefixo, upload, imagem real, proteção, continuidade, composição,
revogação, falhas). API real só em teste opcional, pequeno e autorizado.
