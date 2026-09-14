"""Restauração com a aplicação parada (RF-02 / §9)."""

import shutil
import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Restaura o SQLite a partir de um backup. Pare a aplicação antes."

    def add_arguments(self, parser):
        parser.add_argument("--src", required=True, help="Arquivo de backup (.sqlite3)")

    def handle(self, *args, **options):
        src = Path(options["src"])
        if not src.exists():
            raise CommandError(f"Backup não encontrado: {src}")
        # Valida o backup antes de tocar no banco atual.
        with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as db:
            db.execute("PRAGMA integrity_check").fetchall()
        dst = Path(settings.DATABASES["default"]["NAME"])
        for suffix in ("", "-wal", "-shm", "-journal"):
            p = Path(str(dst) + suffix)
            if p.exists():
                p.unlink()
        shutil.copy2(src, dst)
        self.stdout.write(self.style.SUCCESS(f"Banco restaurado de {src}"))
