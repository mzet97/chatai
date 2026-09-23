# Plano de avaliação (M5)

## Comparativa individual × equipe

Comando: `manage.py agents_eval` → `docs/agents/eval.md` (24/24 verde em
16/09/2026: individual 8/8, equipe 16/16). Determinístico, sem rede, sem
OpenAI: cenários em `chat/services/agents/eval.py`, suíte em
`tests/integration/test_agents_m5_ui_eval.py::test_eval_deterministica_e_toda_verde`.
A equipe não precisa vencer sempre; o relatório registra onde não justifica
o custo (limites declarados, mesmas bases/modelos pertinentes).

## Eixos avaliados separadamente (demais variáveis controladas)

- Orçamento/escopo: `test_ledger_totais_e_bloqueio`, `test_tetos_puros`,
  `test_coordenador_sem_slot_bloqueia_na_primeira` (equipe).
- Replay pai/filhos: `test_agent_thinking_m3.py` (separação pensamento pai/filhos).
- Citações: `tests/integration/test_agents_m5_evidence.py` — só chunk autorizado
  (dono + bases da conversa) vira citação; IDs inventados/alheios e texto livre
  descartados.
- Cache: `test_cache_m2.py` (plano/contabilização simulados); hit remoto **não**
  alegado por teste simulado.
- Multimodal: `test_agente_preserva_imagens_e_ferramentas` (pixels reais ao SDK).

## Experimento real de cache (opcional, autorizado)

Prefixo útil acima do mínimo do modelo, repetição dentro do TTL, leitura dos
campos efetivos de `usage`. TTL 1h fora do CI (sem esperas reais). Sem
aquecimento pago, sem padding, sem promessa de hit. Testes pagos ficam
separados e autorizados; nunca bloqueiam a suíte comum.
