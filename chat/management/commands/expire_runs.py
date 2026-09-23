"""Expira execuções abandonadas (M5; sem tocar nas vinculadas ativas).

Complemento manual da recuperação automática no startup
(`ChatConfig.ready`): marca como `abandoned` as runs não terminais com
heartbeat velho cujo worker morreu. Imagens seguem o mesmo caminho —
nada de payload/base64 em log; listagem e exportação nunca expõem base64.
"""

from django.core.management.base import BaseCommand

from chat.services.generation import STALE_HEARTBEAT_MINUTES
from chat.services.recovery import recover_abandoned_runs


class Command(BaseCommand):
    help = "Marca runs abandonadas (heartbeat velho + worker morto) como abandoned."

    def add_arguments(self, parser):
        parser.add_argument(
            "--stale-minutes",
            type=int,
            default=STALE_HEARTBEAT_MINUTES,
            help="Idade mínima do heartbeat para considerar abandono.",
        )

    def handle(self, *args, **options):
        count = recover_abandoned_runs(stale_minutes=options["stale_minutes"])
        self.stdout.write(f"runs abandonadas expiradas: {count}")
