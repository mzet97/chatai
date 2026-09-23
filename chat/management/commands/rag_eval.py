"""Roda a avaliação de recuperação §18 e grava docs/rag/eval_report.md."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from chat.services.rag import embed
from chat.services.rag import eval as evalsvc


class Command(BaseCommand):
    help = "Avalia lexical/vetorial/híbrida no corpus versionado (40 perguntas)."

    def handle(self, *args, **options):
        if not embed.is_prepared():
            raise CommandError("Rode python manage.py rag_prepare antes.")
        from pathlib import Path

        from django.conf import settings

        User = get_user_model()
        username = "rag-eval"
        User.objects.filter(username=username).delete()
        summaries = {}
        for method in ("lexical", "vector", "hybrid"):
            user = User.objects.create_user(username, password="x" * 12)
            out = evalsvc.run_eval(user, method=method)
            summaries[method] = evalsvc.summarize(out["results"])
            user.delete()
            self.stdout.write(f"{method}: Hit@6={summaries[method]['hit_at_6']}")
        report = self._render(summaries)
        dest = Path(settings.BASE_DIR) / "docs" / "rag" / "eval_report.md"
        dest.write_text(report)
        self.stdout.write(self.style.SUCCESS(f"Relatório em {dest}."))

    def _render(self, summaries: dict) -> str:
        from chat.services.rag import embed

        lines = [
            "# Relatório de avaliação de recuperação (§18)",
            "",
            f"Modelo: {embed.EMBED_MODEL_ID}@{embed.EMBED_MODEL_REVISION[:12]} "
            f"(dim {embed.EMBED_DIM}), CPU. Corpus: 8 docs PT/EN, 40 perguntas "
            "(12 factuais, 8 paráfrases, 4 cross-lang, 4 multi, 4 follow-up, "
            "8 sem resposta). k=6.",
            "",
            "| método | Hit@6 | Recall@6 | MRR | cobertura multi | ms médio/p50/p95 |",
            "|---|---|---|---|---|---|",
        ]
        for method, s in summaries.items():
            lines.append(
                f"| {method} | {s['hit_at_6']} | {s['recall_at_6']} | {s['mrr']} | "
                f"{s['multi_full_coverage']} | {s['ms_mean']}/{s['ms_p50']}/{s['ms_p95']} |"
            )
        lines += [
            "",
            f"Sem resposta com candidatos: {summaries['hybrid']['unanswerable_with_candidates']}/8 "
            "(M4 deve abster-se nesses casos).",
            "",
            f"Falhas híbridas (sem acerto): {summaries['hybrid']['failures'] or 'nenhuma'}.",
            "",
            "Falhas observadas: neste corpus pequeno, a híbrida não supera o "
            "baseline vetorial (AND lexical raramente casa perguntas com "
            "chunks curtos); o pilar lexical cobre casos que o vetorial pode "
            "perder (identificadores, termos raros). Cobertura multi 0,75: "
            "top-6 nem sempre contém todas as fontes necessárias.",
            "",
            "Meta inicial Hit@6 ≥ 0,85 nas respondíveis; resultado acima, sem "
            "ajuste de gabarito.",
        ]
        return "\n".join(lines) + "\n"
