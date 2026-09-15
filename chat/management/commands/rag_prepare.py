"""Preparo explícito do modelo de embeddings (único download autorizado)."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Baixa intfloat/multilingual-e5-small para models/ (licença MIT, origem HF)."

    def handle(self, *args, **options):
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            raise CommandError("sentence-transformers não instalado.") from None
        dest = Path(settings.BASE_DIR) / "models" / "multilingual-e5-small"
        if (dest / "model.safetensors").exists():
            self.stdout.write(f"Modelo já preparado em {dest}.")
            return
        self.stdout.write("Baixando intfloat/multilingual-e5-small (MIT, huggingface.co)...")
        snapshot_download("intfloat/multilingual-e5-small", local_dir=str(dest))
        self.stdout.write(self.style.SUCCESS(f"Preparado em {dest}."))
