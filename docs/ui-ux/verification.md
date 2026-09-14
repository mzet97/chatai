# Verificação do redesign (implementação real, 14/09/2026)

Evidências em `docs/ui-ux/evidence/after-*.png`, capturadas via Playwright/Chromium
contra uvicorn real + banco temporário + fixtures isoladas (sem chave, sem rede
externa, sem custo de API). Antes: `before-*.png` na mesma pasta.

## Cobertura de telas (todas inspecionadas pixel a pixel)

- `after-empty-light.png` — conversa nova: título editorial, 3 sugestões, aviso de
  chave com “Configurar conexão”, compositor na posição estrutural.
- `after-thread-light.png` / `after-thread-dark.png` — thread longa com Markdown
  real: títulos, listas, citação, inline code, bloco `python` com cabeçalho
  (linguagem + “Copiar”), tabela com rolagem interna, bolha do usuário à direita,
  “Copiar resposta” + “Detalhes” no histórico.
- `after-selector-light.png` — seletor aberto: busca + estado de erro honesto
  (“Nenhuma chave configurada”), sem IDs técnicos dominando.
- `after-settings-light.png` — 3 seções (Aparência/Conexão/Padrões), avançado
  recolhido, chave como campo de escrita, último teste persistido.
- `after-inspector-light.png` — painel com Resumo/Contexto/Uso, técnico recolhido,
  turnos incluídos (1, 2) a partir do `context_used` real.
- `after-mobile-nav.png` (390×844) — sidebar sobreposta com scrim; fecha por
  clique fora.
- `after-delete-dialog.png` — menu da linha (Renomear/Arquivar/Excluir…).

## Comandos e resultados observados

- `pytest -q` → **38 passed, 1 skipped** (inclui e2e de navegador real).
- `ruff check chat/` → limpo. `node --check` nos 5 módulos JS → limpo.
- Console do navegador nas capturas: **zero pageerrors**; únicos 404 anteriores
  eram estáticos desatualizados em `staticfiles/` (corrigido com `collectstatic`).
- Contraste calculado (fórmula WCAG): claro 14.95/5.48/5.51/5.96:1,
  escuro 15.10/7.91/8.31/8.31:1 — todos ≥ 4.5:1.
- Scroll (1440×900, claro+escuro): `scrollTop == maxScroll`, último turno
  51.6px acima do compositor (sem sobreposição), pill oculta no fim.
- Teclado: Escape fecha seletor e devolve foco a `#model-btn`; Enter envia;
  botões nativos alcançáveis por Tab; diálogos usam `<dialog>` (foco contido).

## Defeitos encontrados pela inspeção e corrigidos

1. Menu “…” abria sozinho: `.popover.menu { display:flex }` sobrescrevia o
   atributo `hidden` → guarda global `[hidden] { display:none !important }`
   em `tokens.css` (valeu também para `#scroll-pill`).
2. “Detalhes” inexistia no histórico: só aparecia após streaming ao vivo →
   `GET messages` agora expõe `run_id` (1 query extra por página, sem migration)
   e o frontend anexa o botão (verificado no inspetor).
3. Inspetor mostrava “Contexto não registrado” sempre: backend nunca envia
   `snapshot.messages` → seção agora usa `context_used` (seqs incluídos/omitidos).
4. Scrim do mobile nunca aparecia: booleano invertido em `setSide` → corrigido,
   verificado no screenshot 390px.
5. Última mensagem 52px aquém do fim: `refreshKeyBanner()` deslocava o layout
   após o pin de scroll → refixa após o banner (checagem automatizada OK).

## Política base de segurança (novo objetivo, verificado com chave real)

- `chat/policies/corporate_base.md` (versionada `corporate-base-v1`, override por
  `CHAT_BASE_POLICY_PATH`) + `chat/services/base_policy.py` (`compose_system`:
  base sempre primeiro, instruções depois com fronteira explícita).
- `generation.py` envia o composto no `system` (array de blocos / omitido) e
  grava `snapshot.base_policy_version`; campo da conversa renomeado na UI para
  “Instruções da conversa” (contrato `system_prompt` preservado na API/DB).
- Testes: `tests/unit/test_base_policy.py` (4, ordem/precedência/override) +
  prova fim-a-fim com chave real: normal → `ok`; injection
  (“ignore regras + invente senha”) → recusa fundamentada citando prompt
  injection e proteção de credenciais. Nenhum erro de servidor.
- Limite: DLP pré-contagem, inspeção anti-streaming e red-team amplo seguem
  como recomendações não implementadas (camada de defesa, não substitui
  controles do backend).

## Nível de variação (verificado 14/09/2026)

- Backend: `variation.py` (`temp-map-v1`), campo + migration 0002, snapshot com
  `requested_level/temperature_sent/state/reason/temp_map_version`.
- Compatibilidade: docs do provedor + prova ao vivo sem custo (Sonnet 5 +
  temperature → 400 "deprecated"); SDK 1.5.0 sem `temperature` nativo (via
  `extra_body` quando permitido).
- Testes: 25 novos (unit + integração, mocks, sem chave) — mapeamento,
  validação, 409, isolamento, payload real no fake (`extra_body` só quando
  permitido, nunca em count), snapshots imutáveis, retry com nível vigente.
- Navegador (fixtures, sem custo): seletor habilitado em modelo compatível
  (salva sem reload, sem gerar), desabilitado com motivo acessível em Sonnet 5
  (preferência preservada), mobile 390 dark sem overflow, zero pageerrors.
  Capturas `after-level-supported/unsupported/mobile-dark.png`.
- Limites: aceite de temperature em modelo suportado validado só via mock
  (geração paga real não executada); red-team amplo não executado.
- Migração: não havia nenhum campo de temperatura anterior (grep sem
  ocorrências antes da mudança) — nada a preservar/arredondar; default
  `medium` vale para conversas existentes.
- Geração não streaming não existe no app (só `diagnose_generation`, chamada
  de teste sem temperature, inalterada); payload único centralizado no stream.
- Referências efetivamente consultadas em 14/09/2026: docs Messages/create,
  SDK instalado (anthropic 1.5.0, sem `temperature` nativo) e rejeição ao vivo
  no Sonnet 5; demais links do prompt não foram abertos individualmente.

## Pós-entrega (report do usuário, mesma sessão)

- `run_stream` avaliava `request.user` (lazy com DB) dentro de view async →
  `SynchronousOnlyOperation` e HTTP 500 em todo envio sob ASGI. Corrigido com
  avaliação em thread (`sync_to_async`) em `chat/views/api_runs.py`.
- A API passou a exigir `system` como array de blocos: `null`/string davam 400
  (`system: Input should be a valid array`). Novo `_system_param()` em
  `chat/services/generation.py`: array quando há system prompt, omitido quando
  vazio. Verificado fim-a-fim com chave real nos dois casos + suíte verde.

## Limites honestos (não verificado)

- Streaming pago real, Keychain real e zoom de texto 200%: não cobertos por
  teste automatizado; streaming validado só via código + estados de erro.
- 1024×768 e 768×1024: verificados por CSS (breakpoint 64rem) mas sem screenshot
  dedicado; 320px coberto pela regra de overlay, sem captura dedicada.
- Conformidade WCAG integral: contraste calculado + teclado/foco verificados;
  sem auditoria com leitor de tela — não se declara conformidade total.
