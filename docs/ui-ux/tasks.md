# Tarefas do redesign (fatia → verificação) — todas concluídas em 14/09/2026;
detalhes e evidências em `verification.md` + `evidence/after-*.png`.

1. **Tokens + shell + tema** — `tokens.css`, `data-theme`, init sem flash,
   `theme.js` (só tema em localStorage). Verificação: alternar temas sem flash;
   teste existente verde.
2. **Sidebar** — marca, busca com limpar/vazio, grupos por período, menu por
   linha, recolher/reabrir, rodapé. Verificação: screenshot + teclado + e2e.
3. **Tela vazia** — título/apoio/sugestões (preenchem, não enviam), aviso de
   chave. Verificação: clicar sugestão foca compositor com texto, zero chamadas.
4. **Mensagens + Markdown** — reescrever `markdown.js` (blocos, tabelas,
   blockquote, bold corrigido), estilos de leitura, copiar resposta/detalhes,
   `aria-live` contido. Verificação: captura com fixture + teste e2e estendido.
5. **Compositor** — autogrow 2–8 linhas, botão interno com estados, rascunho
   por conversa, pill de scroll, IME intacto. Verificação: navegador + zoom.
6. **Seletor de modelos** — popover com lista real/busca/refresh/estados;
   PATCH na conversa. Verificação: captura aberto + teclado.
7. **Configurações + login + diálogos** — reestruturar `settings.html` em 3
   seções, login no sistema visual, foco em modais, confirmação de exclusão
   com nome. Verificação: captura + teclado/Escape.
8. **Inspetor lateral** — painel Resumo/Contexto/Uso, técnico recolhido.
   Verificação: captura + overlay no mobile.
9. **Responsivo + a11y + final** — 1440/1280/1024/768/390/320, overlay da
   sidebar <1024px, contraste, alvos, reduced-motion, console limpo, suíte
   verde, `docs/ui-ux/verification.md` com antes/depois.
10. **Nível de variação** — `variation.py` + campo + migration 0002 + snapshot
    + seletor no cabeçalho + inspetor. Verificação: 25 testes (mock, sem chave),
    prova no navegador com fixtures, capturas `after-level-*`.
