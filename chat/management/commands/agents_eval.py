"""Gera docs/agents/eval.md a partir da suite sintética (sem pagas, sem rede)."""

from django.core.management.base import BaseCommand

from chat.services.agents import eval as evalsvc


class Command(BaseCommand):
    help = "Avalia individual × equipe na suite sintética e grava docs/agents/eval.md."

    def handle(self, *args, **options):
        from pathlib import Path

        from django.conf import settings

        results = evalsvc.run_eval()
        summary = evalsvc.summarize(results)
        dest = Path(settings.BASE_DIR) / "docs" / "agents" / "eval.md"
        dest.write_text(self._render(results, summary))
        self.stdout.write(
            self.style.SUCCESS(
                f"{summary['all']['passed']}/{summary['all']['total']} "
                f"(individual {summary['individual']['passed']}/"
                f"{summary['individual']['total']}, equipe "
                f"{summary['team']['passed']}/{summary['team']['total']}) → {dest}."
            )
        )

    def _render(self, results, summary):
        lines = [
            "# Avaliação sintética — individual × equipe (AG-6)",
            "",
            "Suite determinística, sem chamadas pagas, sem rede e sem banco:",
            "`chat/services/agents/eval.py` (24 tarefas sobre as camadas puras:",
            "`validate_args`, `validate_delegation`, `validate_child_result`,",
            "`child_catalog`, `budget.check_*`). Cada tarefa declara o veredito",
            "esperado; a suite compara obtido × esperado. Regenere com",
            "`python manage.py agents_eval`.",
            "",
            "## Placar",
            "",
            "| estratégia | acertos | tarefas | taxa |",
            "|---|---|---|---|",
        ]
        for suite in ("individual", "team", "all"):
            s = summary[suite]
            lines.append(f"| {suite} | {s['passed']} | {s['total']} | {s['rate']} |")
        lines += [
            "",
            "Leitura: a estratégia **individual** nunca delega (`not_team`) e",
            "responde direto — passa nas tarefas simples e é bloqueada pelo",
            "orçamento/profundidade como qualquer outra. A estratégia **equipe**",
            "delega o componível (até 2 filhas, profundidade 1, só leitura) e é",
            "recusada com código estruturado nos casos proibidos/estourados.",
            "",
            "## Tarefas",
            "",
            "| id | estratégia | tarefa | esperado | obtido | passou |",
            "|---|---|---|---|---|---|",
        ]
        for r in results:
            mark = "sim" if r["passed"] else "**NÃO**"
            lines.append(
                f"| {r['id']} | {r['suite']} | {r['title']} "
                f"| `{r['expected']}` | `{r['got']}` | {mark} |"
            )
        failures = [r["id"] for r in results if not r["passed"]]
        lines += [
            "",
            f"Falhas: {', '.join(failures) if failures else 'nenhuma'}.",
            "",
        ]
        return "\n".join(lines)
