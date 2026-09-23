# Avaliação sintética — individual × equipe (AG-6)

Suite determinística, sem chamadas pagas, sem rede e sem banco:
`chat/services/agents/eval.py` (24 tarefas sobre as camadas puras:
`validate_args`, `validate_delegation`, `validate_child_result`,
`child_catalog`, `budget.check_*`). Cada tarefa declara o veredito
esperado; a suite compara obtido × esperado. Regenere com
`python manage.py agents_eval`.

## Placar

| estratégia | acertos | tarefas | taxa |
|---|---|---|---|
| individual | 8 | 8 | 1.0 |
| team | 16 | 16 | 1.0 |
| all | 24 | 24 | 1.0 |

Leitura: a estratégia **individual** nunca delega (`not_team`) e
responde direto — passa nas tarefas simples e é bloqueada pelo
orçamento/profundidade como qualquer outra. A estratégia **equipe**
delega o componível (até 2 filhas, profundidade 1, só leitura) e é
recusada com código estruturado nos casos proibidos/estourados.

## Tarefas

| id | estratégia | tarefa | esperado | obtido | passou |
|---|---|---|---|---|---|
| E01 | team | Resumo delegável é autorizado | `None` | `None` | sim |
| E02 | team | Segunda filha ainda cabe no teto | `None` | `None` | sim |
| E03 | team | Pesquisa com critério explícito é autorizada | `None` | `None` | sim |
| E04 | team | Critério vazio ainda autoriza (só tarefa exige texto) | `None` | `None` | sim |
| E05 | team | Filho retorna produto válido | `None` | `None` | sim |
| E06 | team | Filho pode relatar falha estruturada | `None` | `None` | sim |
| E07 | team | Código embutido é proibido | `args:*` | `args:Campo desconhecido: 'code'.` | sim |
| E08 | team | URL embutida é proibida | `args:*` | `args:Campo desconhecido: 'url'.` | sim |
| E09 | team | Modelo no argumento é proibido | `args:*` | `args:Campo desconhecido: 'model'.` | sim |
| E10 | team | Terceira filha bate no teto | `max_children` | `max_children` | sim |
| E11 | team | Especialista fora dos delegáveis é recusado | `unknown_specialist` | `unknown_specialist` | sim |
| E12 | team | Especialista incompleto é recusado | `specialist_incomplete` | `specialist_incomplete` | sim |
| E13 | team | Filho sem síntese é recusado | `empty_child_summary` | `empty_child_summary` | sim |
| E14 | team | Filho com forma inválida é recusado | `invalid_child_result` | `invalid_child_result` | sim |
| I01 | individual | Resposta direta válida passa | `None` | `None` | sim |
| I02 | individual | Modo Agente nunca delega (not_team) | `not_team` | `not_team` | sim |
| I03 | individual | Modo Chat nunca delega (not_team) | `not_team` | `not_team` | sim |
| I04 | individual | Catálogo do filho não vaza delegate nem escrita | `None` | `None` | sim |
| I05 | individual | Resposta direta vazia é recusada | `empty_child_summary` | `empty_child_summary` | sim |
| I06 | individual | Tarefa simples dispensa delegação (solo capaz) | `None` | `None` | sim |
| B01 | team | Profundidade 1 bloqueia neto | `max_depth` | `max_depth` | sim |
| B02 | team | Orçamento estourado bloqueia antes do efeito | `budget_generations` | `budget_generations` | sim |
| B03 | individual | Teto de filhas: check puro recusa o 3º | `max_children` | `max_children` | sim |
| B04 | individual | Profundidade: check puro recusa nível 1 | `max_depth` | `max_depth` | sim |

Falhas: nenhuma.
