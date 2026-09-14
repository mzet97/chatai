"""Backup consistente via API de backup do SQLite (nunca só copiar o arquivo)."""

import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Backup consistente do SQLite (API backup, funciona com WAL ativo)."

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True, help="Arquivo de destino (.sqlite3)")

    def handle(self, *args, **options):
        src = Path(settings.DATABASES["default"]["NAME"])
        dst = Path(options["out"])
        if not src.exists():
            raise CommandError(f"Banco não encontrado: {src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as from_db:
            with sqlite3.connect(dst) as to_db:
                from_db.backup(to_db)
        self.stdout.write(self.style.SUCCESS(f"Backup gravado em {dst}"))
