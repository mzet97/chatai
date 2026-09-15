"""Preparo explícito do modelo de embeddings (único download autorizado)."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

MODEL_ID = "intfloat/multilingual-e5-small"
# Revisão fixada (verificada em 15/09/2026); espelha EMBED_MODEL_REVISION.
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"


class Command(BaseCommand):
    help = "Baixa intfloat/multilingual-e5-small para models/ (licença MIT, origem HF)."

    def handle(self, *args, **options):
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            raise CommandError("sentence-transformers não instalado.") from None
        dest = Path(settings.BASE_DIR) / "models" / "multilingual-e5-small"
        marker = dest / ".revision"
        if marker.exists() and marker.read_text().strip() == MODEL_REVISION:
            self.stdout.write(f"Modelo já preparado em {dest} ({MODEL_REVISION[:12]}).")
            return
        self.stdout.write(f"Baixando {MODEL_ID}@{MODEL_REVISION[:12]} (MIT, huggingface.co)...")
        snapshot_download(MODEL_ID, revision=MODEL_REVISION, local_dir=str(dest))
        marker.write_text(MODEL_REVISION)
        self.stdout.write(self.style.SUCCESS(f"Preparado em {dest}."))
