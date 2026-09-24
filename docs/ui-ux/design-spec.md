# Especificação de design — redesign claude-chat-local

Referências aproveitadas: AI Elements (anatomia: coluna de leitura, compositor
em superfície própria com ação interna, seletor como popover com busca);
shadcn sidebar (identidade discreta, busca, grupos por período, rodapé com
configurações); W3C/APG (contraste AA, alvos 44px, diálogo modal com foco,
`prefers-reduced-motion`); MDN (reduced-motion). Nada instalado dessas stacks.

## Tokens (`tokens.css`)

Paleta do prompt (fundo #F7F6F2, sidebar #F0EFEB, texto #20211F, acento
#A44732; escuro: #191A18/#141512/#EEEFE9, acento #E6A38D) + derivados:
`--bg-hover`, `--bg-selected`, `--danger: #B3261E` (claro) / `#F2B8B0` (escuro),
`--warning-*`, `--success-*`, `--shadow-pop`, `--code-bg/inline`.
Contraste verificado: texto primário/secundário ≥ 4.5:1 e acento sobre
superfície ≥ 4.5:1 nos dois temas (pares checados na verificação).

Tipografia: interface `-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
código `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`; título vazio
`Georgia, serif` 2rem. Mensagem/compositor 1rem/1.65; sidebar e controles
0.875rem; metadados 0.8125rem. Espaçamento 4/8/12/16/24/32/48.
Ícones: SVG Lucide-like inline (18–20px, área clicável ≥ 44px nos principais),
servidos via includes Django — sem CDN, sem emoji.

Elevação só em popover/diálogo; sem sombra por mensagem; transições 120–180ms;
`prefers-reduced-motion` desliga animação.

## Composição

- Desktop: sidebar 16rem + coluna principal; coluna de leitura 48rem máx.,
  margens 24–32px (16px mobile). Cabeçalho 3.5rem: título à esquerda;
  seletor de modelo + menu à direita.
- Sidebar: marca discreta, "Nova conversa", busca com limpar, grupos
  (Hoje/Últimos 7 dias/Anteriores), linha com título + menu `···` (sempre no
  DOM, visível em hover/`focus-within`, acessível no touch), rodapé
  Configurações/Sair, botão recolher; controle para reabrir sem sidebar.
- Tela vazia: "O que vamos explorar?" (Georgia 2rem), apoio, 3 sugestões que
  preenchem o compositor sem enviar; aviso de chave ausente com link.
- Mensagens: usuário em superfície neutra à direita (≤85%); assistente em
  coluna plena sem balão; turnos com 24–32px; metadados secundários; ações
  discretas por resposta (Copiar resposta, Detalhes quando houver run).
- Markdown real: h/p/ul/ol/blockquote/code/tabelas com rolagem interna;
  code header com linguagem + Copiar (feedback "Copiado").
- Compositor: superfície própria raio 1.125rem, textarea 2–8 linhas + scroll
  interno, placeholder "Escreva sua mensagem…", botão dentro (vazio=off,
  streaming=interromper). Rascunho por conversa preservado; falha não limpa.
  Pill "Ir para a última mensagem" quando longe do fim.
- Seletor: nome amigável + id técnico; busca, refresh real, estados
  (carregando/vazio/erro/desatualizado); popover com teclado, sem preços.
- Configurações: diálogo amplo (tela cheia no mobile) em Aparência (tema
  Sistema/Claro/Escuro, imediato, sem flash) / Conexão (endpoint efetivo,
  origem da chave, último teste ok) / Padrões e avançado recolhido. Conversa
  atual em "Configurações da conversa" no menu do cabeçalho.
- Inspetor: painel direito ~24rem (overlay no mobile), Resumo / Contexto
  enviado / Uso e execução, JSON só em modo técnico recolhido, "Não informado"
  para ausentes, aviso de redução só com dados.
- Microcopy: "Preparando resposta…", "Gerando resposta…", "Resposta
  interrompida", "Não foi possível autenticar. Revise a conexão.",
  "A resposta atingiu o limite de saída.", "Nenhuma conversa encontrada",
  "Histórico salvo neste dispositivo. As mensagens enviadas são processadas
  pela Anthropic." (destino ajustado ao endpoint).

## Nível de variação (Baixo/Médio/Alto) — V1..V8

- V1 Controle: `select` nativo com classe `.model-select` ao lado do seletor de
  modelos; rótulo acessível "Nível de variação" + ajuda "Ajusta a variação das
  próximas respostas. Um nível maior não garante mais precisão." Sem números,
  sem "Qualidade", sem promessa de determinismo.
- V2 Mapeamento (backend, `variation.py`, `temp-map-v1`): low→0.2,
  medium→0.5 (padrão), high→0.8. JS nunca vê números; envia só o nível.
- V3 Compatibilidade em três estados, tabela por ID exato (sem inferência
  lexicográfica, sem `model.supports_temperature` inventado):
  suportados {opus-4-6, opus-4-5-20251101, sonnet-4-5-20250929,
  haiku-4-5-20251001}; não suportados {sonnet-5, opus-5, fable-5, fable-5-1};
  resto e endpoint customizado = unknown. SDK 1.5.0 removeu `temperature` dos
  métodos (vai via `extra_body` quando permitido).
- V4 Fontes (14/09/2026): docs Messages/create (temperature depreciado, pós
  Opus 4.6 rejeita ≠1.0 com 400); prova ao vivo sem custo: Sonnet 5 +
  temperature → 400 "`temperature` is deprecated for this model."
- V5 Persistência: `Conversation.temperature_level` (choices, default medium,
  migration 0002); existentes recebem medium; PATCH valida, recusa
  `temperature` numérico, 409 com valor vigente em geração ativa.
- V6 Snapshot por execução: `requested_level`, `temperature_sent` (nº ou
  null), `temperature_state/reason`, `temp_map_version`; inspetor mostra nível
  + aplicado/não-aplicado (número só no modo técnico).
- V7 Falhas: rejeição do parâmetro → "temperature_unsupported" com mensagem de
  compatibilidade (nunca "chave/saldo"); sem retry automático, sem troca de
  modelo, sem simulação via system prompt/effort/top_p.
- V8 Comportamento: por conversa (pending aplicado na criação antes de gerar);
  salva sem reload, restaura em falha, trava durante geração/salvamento e
  serializa com o envio; thinking nunca enviado pelo app.


---

## Liquid Glass (2026-09-24) — redesign de casca

Substitui a paleta terracota/editorial por linguagem Apple contemporânea
(vidro translúcido, mesh de fundo, acento `#0071e3`/`#0a84ff`, SF/system).

- **Tokens:** `tokens.css` (`--glass-*`, mesh, raios 12–24, motion spring 220ms).
- **Shell:** sidebar/header/compositor com `backdrop-filter` + highlight superior;
  fallback sólido via `@supports not`; `prefers-contrast: more` reduz blur.
- **Header colapsado:** só título + Modelo + ⋯; controles avançados em `#head-adv`
  (toggle “Mais controles”, persistido em `localStorage.cc-head-expanded`).
- **Empty states:** “Nenhuma conversa ainda” × “Nada para «q»” (U2 da auditoria).
- **Login:** cartão glass centralizado (U3).
- **Evidência:** `docs/ui-ux/evidence/liquid/`. Comportamento (API/SSE/tools) intacto;
  e2e expandem o header via `head-expanded` quando necessário.
