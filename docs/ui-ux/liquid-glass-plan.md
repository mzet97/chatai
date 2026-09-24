# Plano de ação — Interface Liquid Glass (estilo Apple moderno)

Data: 2026-09-24 · Estado: **plano** (sem implementação nesta etapa)  
Base: auditoria `docs/ui-ux/evidence/review-2026/` + `design-spec.md` atual  
Restrições: ADR-001 (templates + JS modular, sem SPA), ADR-002 (SSE), sem quebrar
comportamento (chat, tools, RAG, agentes, imagens).

## 1. Objetivo

Redesenhar a casca visual para linguagem **Apple contemporânea / Liquid Glass**:
superfícies de vidro translúcido, profundidade em camadas, cantos generosos,
brilho especular sutil, tipografia do sistema com contraste tipográfico
(controlado), movimento fluido — sem sacrificar legibilidade, a11y nem performance
em máquina local.

**Não é** clonar iOS nem usar assets proprietários. É um sistema próprio
inspirado em *glassmorphism* de alto acabamento (visionOS / “Liquid Glass”).

## 2. Direção visual (commit)

| Dimensão | Escolha |
|---|---|
| Âncora | Painéis de vidro flutuantes sobre “canvas” luminoso (visionOS / dock do macOS) |
| Fundo | Gradiente mesh sutil (não gradiente barato de marketing) + blur de 1 camada atrás do glass |
| Superfícies | `backdrop-filter: blur + saturate`; fill `rgba` frio/neutro; borda 1px `rgba(255,255,255,.35)` light / `.12` dark; highlight superior 1px |
| Raio | 12 / 18 / 24 / 28px (controles / cards / painéis / compositor) |
| Tipografia | `-apple-system, "SF Pro Text", "Segoe UI", …` — títulos 600, corpo 400; **sem** Georgia editorial (mudança consciente de personalidade) |
| Acento | Azul Apple-ish `#0A84FF` (dark) / `#0071E3` (light); danger `#FF453A`; nunca acento terracota legado |
| Sombra | Só em camadas glass (0 8px 32px rgba(0,0,0,.12)); mensagens quase sem sombra |
| Movimento | Spring curto 180–240ms em popovers/press; `prefers-reduced-motion` desliga |
| Modo | Light e dark com o **mesmo** glass (só tokens), sem CSS paralelo |

Fallback obrigatório: sem `backdrop-filter` → fill sólido `--bg-surface` legível
(sem “buraco” no texto).

## 3. O que muda e o que não muda

**Muda (casca):** tokens, layout do shell, sidebar, header, compositor, popovers,
diálogos, settings/login/knowledge/agents, empty state, banners, Markdown chrome
(code header, tabelas).

**Não muda (comportamento):** contratos API, SSE, `chat.js` state machine, tools
em 2 fases, RAG, agentes, snapshots, CSRF, testes de domínio. JS só no que for
necessário a estados de glass (ex.: colapso do header).

**Corrige junto (da auditoria):** U1 header colapsado · U2 empty states distintos ·
U3 login no sistema · U4 settings menos críptica · U5 header mobile · favicon.

## 4. Arquitetura de estilo

```
chat/static/chat/css/
  tokens.css      # cores, glass, raios, sombra, tipografia, motion (fonte única)
  base.css        # reset enxuto, focus, [hidden], reduced-motion  (NOVO)
  shell.css       # app frame, sidebar, header, scrim, inspector
  surface.css     # .glass, .glass-pop, .glass-card, highlights, fallback
  thread.css      # mensagens, markdown, código, citações
  controls.css    # botões, selects, switches, inputs, dialogs
  pages.css       # settings, knowledge, agents, login
  app.css         # DEPRECATED → reexport/compat; remove após migração
```

Regra: **nenhuma cor literal fora de `tokens.css`**. Componentes usam classes
`.glass`, `.glass-thick`, `.glass-quiet` (níveis de blur/opacidade), não utilitários
arbitrários.

## 5. Fases (cada uma termina demonstrável)

### Fase 0 — Baseline e contrato visual (½ dia)
- Capturar “antes” (mesmo script do review-2026).
- `tokens-liquid.css` experimental + página de galeria **só em dev** (`/dev/glass/`
  protegida) com: painel, botão, input, popover, dialog, mensagem user/assistant,
  code block, banner, switch.
- Critério: galeria legível em light/dark, fallback sem blur, contraste ≥ 4.5:1.

### Fase 1 — Tokens + shell (1 dia)
- Substituir `tokens.css` pelos tokens liquid (variáveis com o **mesmo nome**
  `--bg-app`, `--text-primary`, … para não quebrar CSS legado durante a migração).
- Shell: canvas com mesh + camada glass da sidebar e do header.
- Header **colapsado**: título + `Modelo` + `⋯` (resto no menu) — resolve U1/U5.
- Critério: 1440 / 1100 / 390 sem truncar título; suíte verde; sem FOUC de tema.

### Fase 2 — Thread + compositor (1–1,5 dia)
- Leitura: bolha user = glass quiet; assistente = coluna plena sobre canvas.
- Compositor flutuante (glass thick, raio 28) com envio accent.
- Markdown: code header glass, tabelas com filete sutil, blockquote com aresta light.
- Pill de scroll e estados de envio/interromper em “segmented control” discreto.
- Critério: fixture longa legível; copiar/detalhes intactos; IME ok; e2e browser.

### Fase 3 — Popovers, menus, diálogos (1 dia)
- `popover` = cartão glass + destaque superior + sombra macia; seta/origem não
  obrigatória (estilo menu do macOS).
- `⋯` unifica: Config. da conversa · Detalhes · Export · Streaming · Variação ·
  Pensamento · Ferramentas · Fontes · Perfil.
- Diálogos: glass cartão + botão destrutivo em `danger` com confirm (U-delete ok).
- Critério: Escape/devolve foco; alvo ≥ 40px; teclado no menu.

### Fase 4 — Páginas (1 dia)
- **Login** (U3): cartão glass centralizado, sem `form.as_p` cru.
- **Settings** (U4): cards por seção; esconder “(origem: …)” em tooltip; erro em destaque.
- **Knowledge / Agents**: mesma receita de cards; empty state 1-2-3 (U7).
- Critério: capturas das 4 telas; contraste; sem regressão de forms.

### Fase 5 — Motion, polish, a11y (½–1 dia)
- Press scale 0.98, popover scale-in 0.96→1, sidebar slide (só se não reduced-motion).
- Focus ring accent 2px offset (visível em glass).
- Alvos 44px nos primários; `prefers-contrast: more` → borda mais forte / blur menor.
- favicon + console limpo (U8).
- Critério: audit script de novo (review-2026) em light/dark/mobile; zero pageerrors.

### Fase 6 — Remoção de legado + docs (½ dia)
- Apagar CSS morto (`.popover.menu` display bugs, seletores órfãos do terracota).
- Atualizar `design-spec.md` (novo capítulo Liquid Glass) e `verification.md`.
- `pytest` + `ruff` + e2e verdes; screenshot final antes/depois.

**Estimativa total: 5–7 dias úteis** (1 pessoa full no design system; sem tocar
backend).

## 6. Matriz de componentes

| Componente | Nível glass | Onde | Prioridade |
|---|---|---|---|
| Sidebar | thick + blur 40 | shell | P0 |
| Header | quiet + border bottom light | shell | P0 |
| Compositor | thick + shadow | thread | P0 |
| Popover / menu | medium | controles | P0 |
| Dialog | medium + backdrop escuro | global | P0 |
| Mensagem user | quiet | thread | P0 |
| Banner (chave/equipe) | warning tint + quiet | chat | P1 |
| Code block | solid dark glass | markdown | P1 |
| Cards de página | medium | settings/knowledge | P1 |
| Login card | medium + mesh forte | login | P1 |
| Approval card (tools) | medium + accent border | thread | P1 |
| Inspector | thick overlay | lateral | P2 |
| Upload zone | dashed border light | dialogs | P2 |

## 7. Riscos e mitigação

| Risco | Mitigação |
|---|---|
| `backdrop-filter` caro em listas longas | glass só em chrome (sidebar/header/composer/pop); thread sem blur por mensagem |
| Texto ilegível em glass | corpo do chat **nunca** só em blur: fill mínimo + testes de contraste |
| Foco invisível em superfície translúcida | ring accent opaco + offset |
| Regressão de testes e2e (seletores) | manter `id`/`aria-*`; mudar só classes CSS |
| “Temático demais” / brilho kitsch | 1 camada de highlight, zero rainbow, zero gloss 3D |
| Performance em máquina modesta | blur ≤ 2 camadas simultâneas; `will-change` só no popover aberto |

## 8. Critérios de aceite (Definition of Done do redesign)

```text
[ ] Galeria de componentes com tokens únicos
[ ] Light + dark sem CSS paralelo
[ ] Fallback sem backdrop-filter legível
[ ] Header não trunca título em 1100/390 (U1/U5)
[ ] Empty states distintos (U2)
[ ] Login no design system (U3)
[ ] Settings sem poluição “origem:” (U4)
[ ] Contraste ≥ 4.5:1 texto normal (par verificado)
[ ] Focus visível em todos os controles
[ ] prefers-reduced-motion respeitado
[ ] IME / rascunho / interromper / anexos intactos
[ ] pytest + ruff + e2e verdes (sem tocar domínio)
[ ] Capturas finais em docs/ui-ux/evidence/liquid-*
```

## 9. Fora de escopo (neste redesign)

- React/SPA, novos providers, tools/MCP, FASE 0–1 do backend (C1/C2 já feitos).
- Micro-interações com canvas/WebGL; glass animado em video.
- Temas extras (sepia, alto contraste completo) além de `prefers-contrast`.
- iOS app / PWA install.

## 10. Próximo passo recomendado

1. Rodar **Fase 0** (galeria + tokens) e escolher entre 2 direções glass
   (A: mais luminoso/mesh · B: mais sólido/fume) com screenshots.
2. Congelar tokens → Fase 1 no shell real.
3. Só então migrar thread e páginas.

---

*Decisões assumidas (revise se quiser outra direção):* reestilizar o app Django
existente (não app novo); manter pt-BR; acento azul Apple (abandona terracota);
tipografia SF/system (abandona Georgia no empty state).
