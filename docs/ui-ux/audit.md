# Auditoria UI/UX — interface anterior (capturada em 1440×900, Chromium real)

Conta de teste `design`, dados fictícios isolados (`CHAT_DB_PATH` temporário).
Evidências: `/tmp/redesign/before-{empty,chat,settings}.png`.

## Defeitos observados (todos vistos nas capturas ou no código)

1. **Markdown quebrado** (`markdown.js:46`, captura before-chat):
   regex de negrito `\*\*[^*]+\*` exige `*` simples no fim → `**closure**`
   renderiza `closur*`. Sem tabelas (pipes crus), sem blockquote (`>` cru),
   sem títulos/listas. Local: `chat/static/chat/js/markdown.js`.
   Proposta: reescrever o renderizador seguro com blocos de verdade.
2. **Bloco de código desproporcional**: `<pre>` cinza-claro gigante, botão
   "Copiar" dentro do `<pre>` após `<br>`, sem cabeçalho de linguagem nem
   rolagem interna delimitada. Proposta: header com linguagem + copiar.
3. **Contraste fraco**: texto do assistente em cinza claro sobre fundo claro;
   metadados e corpo quase sem distinção. Proposta: tokens com contraste AA.
4. **Cabeçalho lotado**: badge + ⚙ + Contexto + MD + JSON competem com o título.
   Proposta: título à esquerda; seletor de modelo + menu de ações à direita.
5. **Sem seletor de modelo**: badge estático, troca só via diálogo. Proposta:
   popover com lista real, busca e atualização (endpoints existentes).
6. **Tela vazia pobre**: uma frase + compositor; sem sugestões, sem orientação
   sobre chave ausente. Proposta: bloco "O que vamos explorar?" + 3 sugestões.
7. **Sidebar sem identidade, sem agrupamento, sem ações por conversa**
   (renomear/arquivar/excluir só no diálogo), sem recolher, sem limpar busca.
8. **Configurações**: formulário longo de seção única em cards, tudo exposto
   (timeout/endpoint junto do básico). Proposta: Aparência/Conexão/Avançado.
9. **Inspetor**: JSON aberto único, embutido entre cabeçalho e histórico,
   empurrando o chat. Proposta: painel lateral direito com Resumo/Contexto/Uso.
10. **Compositor**: textarea fixa de 3 linhas, botões ao lado, sem autogrow,
    sem estado vazio/interromper bem resolvido, sem "voltar ao fim".
11. **Sem tema escuro**, sem ícones (emoji ⚙ como botão), login com cara de
    formulário padrão do Django.
12. **Leitor de tela**: histórico com `aria-live="polite"` anuncia cada token
    durante streaming. Proposta: `off` durante stream, anúncio contido no fim.

## Não observado (não afirmado como defeito)

Performance com centenas de mensagens, comportamento com zoom 200%+ (será
testado após o redesign), Keychain real, chamada paga real.
