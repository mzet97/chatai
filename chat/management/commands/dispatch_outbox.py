"""M4: publica mensagens pendentes da outbox (dispatcher local-lite)."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Entrega mensagens pendentes da outbox de ingestão."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--max", type=int, default=50)
        parser.add_argument("--no-consume", action="store_true")

    def handle(self, *args, **options):
        from chat.services.ingest.broker import LocalBroker
        from chat.services.ingest.dispatcher import dispatch_pending

        broker = options.get("broker") or LocalBroker()
        result = dispatch_pending(
            broker=broker,
            limit=options["max"],
            consume=not options["no_consume"],
        )
        self.stdout.write(f"delivered={result['delivered']} remaining={result['remaining']}")
