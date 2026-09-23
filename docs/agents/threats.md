# Ameaças — agentes, cache, pensamento

Controles herdados: catálogo = prefs ∩ disponível (`catalog.py:
authorized_catalog`); 2 fases + `check_resume_entry`; aprovação consumo
único (`approvals.py`); segredo nunca em snapshot/log (`CLAUDE.md`).

- T-G1 Injeção via tarefa delegada → efeito no pai. Controles: args sem
  código/credencial/URL/permissões; filho só leitura; retorno validado
  como dado, nunca como ordem; citação do filho revalidada no pai.
- T-G2 Exfiltração pai→filho (contexto amplo liberado). Controles:
  `released_context` mínimo explícito em `Delegation`; sem memória
  global (ADR-A2); `owner` filtra tudo.
- T-G3 Filho como oráculo de escrita (consentimento forjado). Controles:
  escrita exige aprovação que só o dono concede no endpoint decide;
  filho retorna limitação; `valid_for` (approve+consumed+digest exato).
- T-G4 Cache além da permissão (revogação × replay). Controles:
  revogação prevalece; fingerprints incluem dono, conexão,
  agente/versão, contexto e revisão de permissões; TTL só `5m`/`1h`,
  nunca dois por chamada.
- T-G5 Estouro de orçamento (fan-out/DOS pago). Controles: cotas da
  árvore (§C-A4), ledger atômico, retry não renova filha, cancelamento
  em cascata da raiz.
- T-G6 Pensamento vazado entre agentes. Controles: replay com blocos do
  mesmo agente em ordem; resposta do filho nunca vira pensamento do pai;
  resumo só do disponibilizado.
- T-G7 Falsa economia de cache. Controles: só `Usage` confirmado conta;
  ausente = desconhecido; sem `lru_cache`, sem padding, sem preço
  versionado (ADR-A3/A5).
