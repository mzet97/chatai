"""Suite de avaliação sintética M5 (AG-6): individual × equipe.

24 tarefas determinísticas, sem pagas, sem rede e sem banco: exercem só as
camadas puras (`team.validate_args/validate_delegation/validate_child_result/
child_catalog`, `budget.check_*`). Cada tarefa declara o veredito esperado;
`run_eval` compara e `summarize` agrega por estratégia.

Uso: `python manage.py agents_eval` (grava `docs/agents/eval.md`).
"""

from chat.services.agents import budget as _budget
from chat.services.agents import team as _team

SPEC_UUID = "11111111-1111-1111-1111-111111111111"
OTHER_UUID = "22222222-2222-2222-2222-222222222222"

_BASE_CTX = {
    "mode": "team",
    "delegatable_ids": [SPEC_UUID],
    "specialist_uuid": SPEC_UUID,
    "specialist_complete": True,
    "existing_children": 0,
    "max_child_runs": _budget.MAX_CHILD_RUNS,
    "parent_depth": 0,
    "budget_summary": None,
    "elapsed_s": 0.0,
}

_VALID_ARGS = {
    "specialist": SPEC_UUID,
    "task": "Resumir o capítulo 2 em 5 itens.",
    "completion_criteria": "5 itens, cada um com página citada.",
}


def _ctx(**over):
    ctx = dict(_BASE_CTX)
    ctx.update(over)
    return ctx


def _args(**over):
    args = dict(_VALID_ARGS)
    args.update(over)
    return args


def _scenario_delegate(args, ctx):
    """None = delegação autorizada; senão o código de recusa."""
    err = _team.validate_args(args)
    if err:
        return f"args:{err}"
    return _team.validate_delegation(**ctx)


def _scenario_child(output):
    """None = resultado aceito; senão o código de recusa."""
    _, err = _team.validate_child_result(output)
    return err


def _scenario_catalog_safe():
    """Catálogo do filho: sem delegate_to_agent, sem escrita."""
    from chat.services.tools.registry import ToolRecord

    parent = [
        ToolRecord(
            stable_id="local:calculate",
            origin="local",
            original_name="calculate",
            description="calc",
            input_schema={"type": "object"},
            version="v1",
            approval="auto",
        ),
        ToolRecord(
            stable_id="local:create_study_note",
            origin="local",
            original_name="create_study_note",
            description="escrita",
            input_schema={"type": "object"},
            version="v1",
            approval="require",
        ),
        _team.delegate_record(),
    ]
    child = _team.child_catalog(parent)
    ids = [r.stable_id for r in child]
    if _team.DELEGATE_STABLE_ID in ids:
        return "delegate_leaked"
    if "local:create_study_note" in ids:
        return "write_leaked"
    if "local:calculate" not in ids:
        return "read_lost"
    return None


def _scenario_solo(args):
    """Individual nunca delega: validate_delegation fora de Equipe recusa."""
    err = _team.validate_args(args)
    if err:
        return f"args:{err}"
    return _team.validate_delegation(**_ctx(mode="agent"))


TASKS = [
    # ---- Equipe: delegações válidas (6) ----
    {
        "id": "E01",
        "suite": "team",
        "title": "Resumo delegável é autorizado",
        "run": lambda: _scenario_delegate(_args(), _ctx()),
        "expect": None,
    },
    {
        "id": "E02",
        "suite": "team",
        "title": "Segunda filha ainda cabe no teto",
        "run": lambda: _scenario_delegate(_args(), _ctx(existing_children=1)),
        "expect": None,
    },
    {
        "id": "E03",
        "suite": "team",
        "title": "Pesquisa com critério explícito é autorizada",
        "run": lambda: _scenario_delegate(
            _args(
                task="Listar 3 fontes sobre o tema com ano.",
                completion_criteria="3 fontes com ano e página.",
            ),
            _ctx(),
        ),
        "expect": None,
    },
    {
        "id": "E04",
        "suite": "team",
        "title": "Critério vazio ainda autoriza (só tarefa exige texto)",
        "run": lambda: _scenario_delegate(_args(completion_criteria=""), _ctx()),
        "expect": None,
    },
    {
        "id": "E05",
        "suite": "team",
        "title": "Filho retorna produto válido",
        "run": lambda: _scenario_child(
            {
                "state": "done",
                "summary": "5 itens resumidos.",
                "evidence": ["p.12"],
                "limitations": "cap.2 apenas",
                "failure": "",
            }
        ),
        "expect": None,
    },
    {
        "id": "E06",
        "suite": "team",
        "title": "Filho pode relatar falha estruturada",
        "run": lambda: _scenario_child(
            {"state": "failed", "summary": "Fonte indisponível.", "failure": "timeout"}
        ),
        "expect": None,
    },
    # ---- Equipe: recusas obrigatórias (8) ----
    {
        "id": "E07",
        "suite": "team",
        "title": "Código embutido é proibido",
        "run": lambda: _scenario_delegate(_args(code="print(1)"), _ctx()),
        "expect_prefix": "args:",
    },
    {
        "id": "E08",
        "suite": "team",
        "title": "URL embutida é proibida",
        "run": lambda: _scenario_delegate(_args(url="https://x.example/"), _ctx()),
        "expect_prefix": "args:",
    },
    {
        "id": "E09",
        "suite": "team",
        "title": "Modelo no argumento é proibido",
        "run": lambda: _scenario_delegate(_args(model="outro-modelo"), _ctx()),
        "expect_prefix": "args:",
    },
    {
        "id": "E10",
        "suite": "team",
        "title": "Terceira filha bate no teto",
        "run": lambda: _scenario_delegate(_args(), _ctx(existing_children=2)),
        "expect": "max_children",
    },
    {
        "id": "E11",
        "suite": "team",
        "title": "Especialista fora dos delegáveis é recusado",
        "run": lambda: _scenario_delegate(_args(), _ctx(specialist_uuid=OTHER_UUID)),
        "expect": "unknown_specialist",
    },
    {
        "id": "E12",
        "suite": "team",
        "title": "Especialista incompleto é recusado",
        "run": lambda: _scenario_delegate(_args(), _ctx(specialist_complete=False)),
        "expect": "specialist_incomplete",
    },
    {
        "id": "E13",
        "suite": "team",
        "title": "Filho sem síntese é recusado",
        "run": lambda: _scenario_child({"state": "done", "summary": "  "}),
        "expect": "empty_child_summary",
    },
    {
        "id": "E14",
        "suite": "team",
        "title": "Filho com forma inválida é recusado",
        "run": lambda: _scenario_child(["não-um-dict"]),
        "expect": "invalid_child_result",
    },
    # ---- Individual: caminho solo (6) ----
    {
        "id": "I01",
        "suite": "individual",
        "title": "Resposta direta válida passa",
        "run": lambda: _scenario_child({"state": "done", "summary": "Resposta direta."}),
        "expect": None,
    },
    {
        "id": "I02",
        "suite": "individual",
        "title": "Modo Agente nunca delega (not_team)",
        "run": lambda: _scenario_solo(_args()),
        "expect": "not_team",
    },
    {
        "id": "I03",
        "suite": "individual",
        "title": "Modo Chat nunca delega (not_team)",
        "run": lambda: _team.validate_delegation(**_ctx(mode="chat")),
        "expect": "not_team",
    },
    {
        "id": "I04",
        "suite": "individual",
        "title": "Catálogo do filho não vaza delegate nem escrita",
        "run": _scenario_catalog_safe,
        "expect": None,
    },
    {
        "id": "I05",
        "suite": "individual",
        "title": "Resposta direta vazia é recusada",
        "run": lambda: _scenario_child({"state": "done", "summary": ""}),
        "expect": "empty_child_summary",
    },
    {
        "id": "I06",
        "suite": "individual",
        "title": "Tarefa simples dispensa delegação (solo capaz)",
        "run": lambda: _scenario_child({"state": "done", "summary": "2+2=4.", "evidence": []}),
        "expect": None,
    },
    # ---- Orçamento e profundidade (4, ambas as estratégias) ----
    {
        "id": "B01",
        "suite": "team",
        "title": "Profundidade 1 bloqueia neto",
        "run": lambda: _scenario_delegate(_args(), _ctx(parent_depth=1)),
        "expect": "max_depth",
    },
    {
        "id": "B02",
        "suite": "team",
        "title": "Orçamento estourado bloqueia antes do efeito",
        "run": lambda: _scenario_delegate(
            _args(),
            _ctx(
                budget_summary={
                    "generations": 99,
                    "invocations": 0,
                    "child_runs": 0,
                    "output_tokens_used": 0,
                }
            ),
        ),
        "expect": "budget_generations",
    },
    {
        "id": "B03",
        "suite": "individual",
        "title": "Teto de filhas: check puro recusa o 3º",
        "run": lambda: _budget.check_child_count(2),
        "expect": "max_children",
    },
    {
        "id": "B04",
        "suite": "individual",
        "title": "Profundidade: check puro recusa nível 1",
        "run": lambda: _budget.check_depth(1),
        "expect": "max_depth",
    },
]


def run_eval():
    """Roda as 24 tarefas; retorna lista de {id, suite, title, expected, got, passed}."""
    results = []
    for task in TASKS:
        try:
            got = task["run"]()
        except Exception as exc:  # nunca derruba a suite
            got = f"raised:{type(exc).__name__}"
        if "expect_prefix" in task:
            passed = isinstance(got, str) and got.startswith(task["expect_prefix"])
            expected = task["expect_prefix"] + "*"
        else:
            passed = got == task["expect"]
            expected = task["expect"]
        results.append(
            {
                "id": task["id"],
                "suite": task["suite"],
                "title": task["title"],
                "expected": expected,
                "got": got,
                "passed": passed,
            }
        )
    return results


def summarize(results):
    """Agrega por estratégia: {suite: {passed, total, rate}} + total geral."""
    out = {}
    for suite in ("individual", "team"):
        rows = [r for r in results if r["suite"] == suite]
        passed = sum(1 for r in rows if r["passed"])
        out[suite] = {
            "passed": passed,
            "total": len(rows),
            "rate": round(passed / len(rows), 3) if rows else 0.0,
        }
    passed = sum(1 for r in results if r["passed"])
    out["all"] = {
        "passed": passed,
        "total": len(results),
        "rate": round(passed / len(results), 3) if results else 0.0,
    }
    return out
