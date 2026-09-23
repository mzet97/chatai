"""Worker de execução LLM (M2, local-lite): um processo, fila SQLite.

Reivindica runs `queued` (compare-and-set), executa o MESMO
`execute_run` da via HTTP (sem segundo executor) e grava cada evento
confirmado no diário. Fechar a aba não cancela: só `cancel` interrompe.
Ctrl+C para parar.
"""

from __future__ import annotations

import asyncio
import socket
import time

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand

from chat.models import GenerationRun
from chat.services.generation import execute_run
from chat.services.runtime import claims, journal


def _worker_id() -> str:
    import os

    return f"{socket.gethostname()}:{os.getpid()}"


async def _process(run: GenerationRun, worker_id: str) -> str:
    def _load():
        return GenerationRun.objects.select_related("conversation__owner").get(pk=run.pk)

    fresh = await sync_to_async(_load)()
    user = fresh.conversation.owner
    await sync_to_async(journal.append)(
        fresh, "run_started", {"state": "running", "worker": worker_id}
    )
    final = "done"
    async for event in execute_run(str(fresh.uuid), user=user, client=None):
        await sync_to_async(journal.append)(fresh, event.get("type", "event"), _project(event))
        await sync_to_async(claims.heartbeat)(fresh)
        if event.get("type") in ("error", "cancelled"):
            final = event.get("type")
    await sync_to_async(journal.append)(fresh, "run_finished", {"terminal": final})
    return final


def _project(event: dict) -> dict:
    """Projeção liberada do evento para o diário (sem texto integral duplicado
    além do necessário à reconstrução: deltas pequenos passam, blocos
    protocolares ficam nas tabelas próprias)."""
    keep = {}
    for key in ("type", "code", "message", "state", "text", "seq", "step", "kind"):
        if key in event and isinstance(event[key], (str, int, float, bool)):
            keep[key] = event[key]
            if len(str(keep)) > 4000:
                break
    return keep


class Command(BaseCommand):
    help = "Executa runs queued do diário durável (M2). Ctrl+C para parar."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Uma passada e sai.")
        parser.add_argument("--poll", type=float, default=2.0, help="Segundos entre passadas.")

    def handle(self, *args, **options):
        worker_id = _worker_id()
        self.stdout.write(f"run_worker {worker_id}: aguardando runs queued.")
        while True:
            run = claims.claim_next(worker_id)
            if run is None:
                if options["once"]:
                    return
                time.sleep(options["poll"])
                continue
            final = asyncio.run(_process(run, worker_id))
            state = GenerationRun.objects.filter(pk=run.pk).values_list("state", flat=True).first()
            self.stdout.write(f"run {run.uuid} -> {final} (state={state})")
            if options["once"]:
                return
