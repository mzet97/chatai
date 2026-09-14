# Especificação — claude-chat-local

Interface e armazenamento locais (SQLite em `data/chat.sqlite3`); a inferência ocorre na
API da Anthropic — o contexto selecionado sai do computador. Essa distinção aparece na
interface (aviso) e no README.

## Requisitos funcionais

### RF-01 — Interface principal (pt-BR)
- Barra lateral: novo chat, busca local, conversas ordenadas por atualização.
- Área central: histórico com autor (usuário/assistente), horário e estado da resposta.
- Cabeçalho: título, modelo selecionado, acesso a configurações da conversa.
- Compositor: caixa multilinha; Enter envia, Shift+Enter quebra linha; respeitar IME
  (não enviar durante `compositionstart`–`compositionend`).
- Configurações: conexão/SDK, padrões, diagnóstico. Painel didático recolhível:
  contexto enviado, modelo, parâmetros, tokens.
- Uma geração ativa por conversa: bloqueia novo envio na mesma conversa; leitura livre.
- Estados: vazio, carregando, desconexão, erro. Autoscroll só quando já no fim.
  Foco visível, rótulos acessíveis, layout utilizável em telas menores.
- Markdown + blocos de código com botão copiar. Sanitização obrigatória: sem `innerHTML`
  sobre deltas; texto seguro durante streaming, formatação ao concluir; sem imagens
  remotas automáticas.

### RF-02 — Ciclo de vida dos chats
Criar, abrir, renomear, arquivar/desarquivar, excluir com confirmação. Busca por título
e conteúdo com paginação, restrita ao usuário. Título inicial por regra local
(primeiros ~60 caracteres da primeira mensagem), sem chamada à IA. Persistência
independente de aba/cookie/processo. Exportação JSON versionada + Markdown, sem
credenciais; exclusão remove dados associados (sem promessa sobre cópias externas).

### RF-03 — Modelo e instruções por conversa
Modelo + system prompt + limites por conversa. Troca afeta só chamadas futuras; cada
resposta registra seu modelo real. Isolamento total entre conversas. Mudança de config
durante geração não altera a chamada em andamento (snapshot imutável).

### RF-04 — Padrões e precedência
Funcionar sem configuração avançada se houver chave válida. `.env.example` com valores
de aplicação (não limites oficiais). Precedência (não secretas): conversa → preferência
da interface → ambiente → `.env` → padrão. Credencial: Keychain selecionada → ambiente
→ `.env`. Origem efetiva visível, segredo nunca. `.env` resolvido pela raiz do projeto.

### RF-05 — Configuração do SDK
Modelo padrão, timeout, retries limitados, conexão. Sem `temperature`/`top_p`/thinking/beta
por padrão. Chave é campo de escrita (vazio = manter; remoção ação própria). Keychain via
`keyring`; no SQLite só referência/metadados. Sem fallback silencioso para texto puro;
sem chave em localStorage/cookies. Troca de domínio exige confirmação; allowlist no
servidor; HTTPS obrigatório; sem promessa "OpenAI-compatible".

### RF-06 — Diagnóstico real
Passos distintos: (1) config carregada, (2) cliente instanciado, (3) autenticação via
listagem de modelos, (4) geração validada por chamada pequena explicitamente autorizada.
Diferenciar 401/403/modelo indisponível/429/timeout/conexão/erro servidor.

### RF-07 — Modelos reais
Models API com paginação completa; sem lista manual fixa. Cache local com horário/perfil/
endpoint; invalidação ao trocar credencial/endpoint; atualização manual; indicar
desatualização. Resolução: conversa → padrão do usuário → `ANTHROPIC_MODEL` →
`claude-sonnet-5`, conferindo disponibilidade. Candidato ausente: pedir escolha; sem
substituição silenciosa. Falha de consulta: preservar identificador, informar não
revalidado.

### RF-08 — Reconstrução por chamada
API stateless: payload reconstruído do SQLite. `system` no nível superior; `messages`
só com `user`/`assistant`. Ordem determinística: turnos anteriores válidos + mensagem
atual exatamente uma vez; só resposta aceita por turno. Falhas/canceladas/interrompidas
fora do contexto automático, visíveis no histórico. Retry reutiliza a pergunta original.

### RF-09 — Orçamento e redução
Enviar histórico elegível enquanto couber; `messages.count_tokens` antes de gerar.
Remover turnos completos mais antigos; preservar ordem, system e mensagem atual;
recontar. Sem corte no meio nem resposta órfã. Indicar turnos omitidos; modo estrito
bloqueia em vez de reduzir. System+atual acima do limite: não chamar, orientar redução.
Sem resumo por IA na v1. Falha de contagem: interromper com erro recuperável
(estimativas didáticas marcadas como estimativas).

### RF-10 — Inspeção didática
Painel por execução: system prompt, mensagens incluídas/omitidas, payload sem
credenciais, orçamento, contagem, uso final. Snapshot da execução, não config atual.
Sem auth headers; sem log de conteúdo por padrão.

### RF-11 — Fluxo de geração
POST com CSRF; SSE via `fetch`; eventos `run_started`, `text_delta`, `usage`, `done`,
`error`, `cancelled`. Parser tolera frames/UTF-8 fragmentados. Erro pós-início via
protocolo, sem trocar status HTTP. Backend: validar → reservar execução (transação
curta) → construir/contar contexto (fora de escrita longa) →
`AsyncAnthropic.messages.stream` → deltas + checkpoints espaçados →
`get_final_message()` → persistir resposta/metadados → `done` só após persistência.
Sem transação aberta em espera de rede; sem SDK síncrono no loop; ORM em funções sync
via `sync_to_async`. Renderizar blocos de texto disponíveis; v1 sem tools/thinking.
Todo evento de dados carrega `run_id` + `seq` monotônica; `done` é canônico (traz o
texto final persistido). `Cache-Control: no-store, no-transform`; heartbeat via
comentários SSE sem segundo consumidor do iterador.

### RF-14 — Modo de entrega por conversa (Streaming)
Switch **Streaming** no header (item equivalente no menu ⋯ no mobile), persistido em
`Conversation.response_mode` (`streaming`| `complete`; padrão `streaming`; migration
`0003`, sem reescrever histórico). Ligado: deltas em tempo real. Desligado: estado de
espera e só a resposta final liberada — nenhum `text_delta` trafega na rede. O seletor
controla a ENTREGA ao navegador, não a chamada ao provedor: ambos os modos usam o
mesmo `client.messages.stream` (uma chamada por tentativa), com mesmo modelo,
temperatura (Baixo/Médio/Alto), system, histórico e limite. Snapshot imutável registra
`requested_response_mode`, `effective_response_mode` e motivo (sem gate de revisão
prévia no projeto, efetivo = solicitado). PATCH com 409 durante geração ativa; sem
recarregar a página; falha restaura o estado anterior com o rascunho intacto.
Cancelamento emite `cancelled`; fechar sem `done` não é sucesso (consulta o estado
salvo). Em queda abrupta, o último fragmento exibido pode estar à frente do último
checkpoint — durabilidade por token não é prometida.

### RF-12 — Interrupção e recuperação
Interromper aborta leitura e fecha stream upstream; preserva parcial como
cancelada/interrompida, nunca concluída. Sem promessa de custo zero. Fechar aba =
interromper. Reabrir mostra persistido, sem auto-gerar. Recuperação de execuções
abandonadas por heartbeat verificável. Retry explícito do último turno falho, nova
tentativa, sem duplicar pergunta. `stop_reason=max_tokens`: truncamento sinalizado,
sem continuação automática paga.

### RF-13 — Idempotência e concorrência
Chave de idempotência + hash por envio; repetição retorna estado existente; mesma chave
com outro conteúdo = conflito 409. Uma execução ativa por conversa via update atômico
(SQLite; sem `select_for_update`). Retry = nova tentativa. Toda saída libera a
exclusividade. Geração padrão: zero retries automáticos.

## Requisitos não funcionais

### RNF-01 — Fronteiras
`127.0.0.1` padrão; `ALLOWED_HOSTS` sem curingas; CSRF em mutações; consultas filtradas
por proprietário; bootstrap de usuário local sem senha padrão. Backend único a chamar a
Anthropic; chave nunca em HTML/JS/JSON/logs/fixtures. `.env`, banco, WAL/SHM, backups e
logs fora do Git; permissões restritivas. SQLite local não é "criptografado".
Sem telemetria; sem CDN obrigatório (assets locais).

### RNF-02 — Custos
Tokens por resposta e agregados por conversa; incompletos marcados. Sem saldo/preço/
dólares inventados. Logs: duração, status, request ID; sem corpo de conversa.

## Histórias de usuário
1. Como usuário local, quero conversar com streaming para acompanhar respostas longas.
2. Como usuário, quero fechar e reabrir sem perder chats.
3. Como estudante, quero ver o contexto enviado para aprender o SDK.
4. Como usuário, quero diagnosticar chave/endpoint sem adivinhar o erro.

## Fora da v1 (backlog)
Anexos, OCR, RAG, ferramentas executáveis, edição com ramificações, compartilhamento
público, sincronização entre máquinas, resumo automático por IA, estimativa de custo.

## Critérios de aceite
Suíte `pytest` verde sem chave/rede; fluxos críticos cobertos; README executável de
pasta limpa; `verification.md` com evidências reais; nenhum TODO no caminho principal.
