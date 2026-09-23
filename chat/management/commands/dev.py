"""Desenvolvimento: servidor ASGI + worker de ingestão no mesmo terminal.

Uso: .venv/bin/python manage.py dev [--port 8000] [--no-worker]
Encerra os dois processos com Ctrl+C. Sem shell arbitrário: apenas os dois
comandos documentados do projeto.
"""

import os
import subprocess
import sys

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Sobe uvicorn (app+estáticos) e rag_worker juntos (só desenvolvimento)."

    def add_arguments(self, parser):
        parser.add_argument("--port", type=int, default=8000)
        parser.add_argument("--no-worker", action="store_true",
                            help="Sobe só o servidor (fila fica parada).")

    def handle(self, *args, **options):
        procs = []
        try:
            procs.append(subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "config.asgi:application",
                 "--host", "127.0.0.1", "--port", str(options["port"])],
            ))
            self.stdout.write(f"servidor em http://127.0.0.1:{options['port']}")
            if not options["no_worker"]:
                from django.conf import settings

                manage = str(settings.BASE_DIR / "manage.py")
                env = dict(os.environ)
                procs.append(subprocess.Popen(
                    [sys.executable, manage, "rag_worker"], env=env,
                ))
                self.stdout.write("rag_worker ativo (heartbeat em "
                                  ".worker_heartbeat do RAG_STORAGE_DIR).")
            else:
                self.stdout.write("sem worker: uploads ficam 'Na fila'.")
            for p in procs:
                p.wait()
        except KeyboardInterrupt:
            pass
        finally:
            for p in procs:
                if p.poll() is None:
                    p.terminate()
            for p in procs:
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=10)
