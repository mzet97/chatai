"""Worker de ingestão RAG: fila SQLite, um processo, sem thread de view."""

import socket
import time

from django.core.management.base import BaseCommand

from chat.services.rag.heartbeat import beat
from chat.services.rag.worker import claim_next_job, process_job


class Command(BaseCommand):
    help = "Processa a fila de ingestão RAG (extração→publicação). Ctrl+C para parar."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Uma passada e sai.")
        parser.add_argument("--max-jobs", type=int, default=10)
        parser.add_argument("--poll", type=float, default=5.0, help="Segundos entre passadas.")

    def handle(self, *args, **options):
        worker_id = f"{socket.gethostname()}:{__import__('os').getpid()}"
        self.stdout.write(f"rag_worker {worker_id}: aguardando jobs (offline = fila parada).")
        while True:
            beat(worker_id)
            job = claim_next_job(worker_id)
            if job is None:
                if options["once"]:
                    return
                time.sleep(options["poll"])
                continue
            final = process_job(job.uuid, worker_id)
            self.stdout.write(f"job {job.uuid} -> {final}")
            if options["once"]:
                return
