# Agentes, cache e pensamento — Especificação SDD

Verificado no ambiente em 15/09/2026 (SDK `anthropic==1.5.0`, Django 5.2).
«Desconhecido» = controle desabilitado, nunca presumido. Client SDK Python
(`AsyncAnthropic` + Messages API); sem Agent SDK/CLI (ver ADR-1 em `adr.md`).

## AG-1. Modos e perfis

- AG-1.1 Modos por conversa: **Chat** (existente, sem delegação),
  **Agente** (perfil individual + loop) e **Equipe** (coordenador + até dois
  especialistas, profundidade um; sem fan-out obrigatório).
- AG-1.2 `AgentDefinition` (owner, nome, descrição, instruções, modelo,
  fontes/ferramentas permitidas, delegáveis, pensamento, variação, cache,
  limites) + `AgentVersion` imutável publicada; arquivar impede seleção,
  preserva proveniência. Ação **Criar agentes de exemplo** idempotente
  (Geral/Pesquisador/Revisor/Coordenador), sem pagas nem permissões
  automáticas; perfil incompatível marcado incompleto.
- AG-1.3 Precedência do principal: política/tetos → override da conversa →
  versão do agente → padrões. Subagente: tetos/escopo da raiz → versão do
  especialista. Valores efetivos + origem visíveis; mudança bloqueada com
  geração/aprovação pendente.

## AG-2. Delegação controlada

- AG-2.1 Ferramenta interna `delegate_to_agent` só ao coordenador em Equipe:
  (especialista ∈ permitidos, tarefa, critério, referências). Sem código,
  prompt privilegiado, credencial, URL, permissões ou modelo nos argumentos.
- AG-2.2 `AgentRun` filho persistido; config resolvida no backend; sem
  delegação nos filhos; máx 2 filhas (retry não renova); paralelas só
  independentes (reserva atômica, correlação por IDs).
- AG-2.3 Filho retorna objeto validado (estado, síntese, evidências,
  limitações, falha). Só leitura nos filhos; consentimento → limitação
  retornada. Pai recebe produto + referências revalidadas (citação do filho
  nunca é prova; índices `search_result` não cruzam payloads).
- AG-2.4 Imagens só ao agente com visão + necessidade; sem propagação geral.

## AG-3. Orçamento, interrupção, recuperação

- AG-3.1 Tetos da árvore: 10 gerações, 12 invocações, 2 filhas, profundidade 1,
  2 simultâneas, 12.000 tokens saída confirmada+reservada, 180 s ativas.
  Ledger: reserva `max_tokens` antes de cada chamada + entrada estimada +
  uso confirmado; ambiguidade → margem conservadora; sem "última resposta"
  fora do limite. Cancelar a raiz fecha tudo estruturadamente (sem rollback).
- AG-3.2 Checkpoints + retomada explícita sem repetir concluídas; `RunEvent`
  sequenciado para UI/recuperação; só a raiz ocupa a exclusividade.

## AG-4. Pensamento por agente (reutiliza `thinking.py`)

- AG-4.1 Padrão/Desativado/Ativado + Baixo/Médio/Alto por perfil, mesma
  resolução (`effort`, orçamento, `display`); sem `xhigh`/`max`; conflitos
  com temperatura resolvidos antes da chamada. Resumo só do disponibilizado.
- AG-4.2 Replay: blocos do mesmo agente em ordem; resposta do filho nunca
  vira pensamento do pai; dificuldade de cache ≠ invalidade de pensamento.

## AG-5. Prompt caching real (nunca `lru_cache`)

- AG-5.1 Modos por perfil + override: desativado | estável (breakpoint no fim
  das instruções aprovadas; ferramentas determinísticas) | conversa (top-level
  `cache_control` confirmado). TTL 5 min padrão, 1 h explícita; nunca dois
  TTLs/estratégias por chamada; sem marcar `thinking`; sem padding abaixo do
  limiar ("não elegível"); sem botão de limpeza sem API oficial.
- AG-5.2 `ModelStep` passa a persistir `cache_creation_input_tokens`,
  `cache_read_input_tokens` (+ detalhe TTL quando houver); ausente =
  desconhecido. Fórmulas: `entrada_total = sem_cache + gravada + lida`;
  `fração = lida / total` (zero protegido). UI separa solicitado, elegível,
  escrita/leitura confirmadas e sem confirmação. Só tokens, sem tabela de
  preços versionada — sem estimativa monetária.
- AG-5.3 Revogação prevalece sobre cache/replay; fingerprints incluem dono,
  conexão, agente/versão, contexto e revisão de permissões.

## AG-6. UI, avaliação e aceite

Página **Agentes** (CRUD, publicar, arquivar, exemplos), seletor
Chat/Agente/Equipe, trilha da equipe, métricas, RAG/imagens/MCP preservados.
Avaliação: ≥20 tarefas sintéticas, individual × equipe. Aceite exige fluxo
real na UI + suite mock + navegador; API real só em teste opcional autorizado.
