"""Recuperação de execuções abandonadas (RF-12).

Critério verificável: run em estado não terminal cujo heartbeat está velho E cujo
worker (pid) não está mais vivo — ou cujo worker é o próprio processo atual no
startup (reiniciamos: o stream remoto foi perdido e não será retomado).
Nunca marca execuções realmente ativas como abandonadas.
"""

import os
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from chat.models import TERMINAL_RUN_STATES, Conversation, GenerationRun
from chat.services.generation import STALE_HEARTBEAT_MINUTES


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, OverflowError):
        return False


def recover_abandoned_runs(*, stale_minutes: int = STALE_HEARTBEAT_MINUTES) -> int:
    cutoff = timezone.now() - timedelta(minutes=stale_minutes)
    mine = os.getpid()
    count = 0
    stale = GenerationRun.objects.exclude(state__in=TERMINAL_RUN_STATES).filter(
        last_heartbeat__lt=cutoff
    )
    for run in stale.iterator():
        if run.worker_pid == mine or not _pid_alive(run.worker_pid):
            with transaction.atomic():
                run.state = "abandoned"
                run.error_code = "abandoned"
                run.error_message = "Execução abandonada por encerramento inesperado."
                run.finished_at = timezone.now()
                run.save()
                if run.assistant_message_id:
                    from chat.models import Message

                    Message.objects.filter(pk=run.assistant_message_id, state="partial").update(
                        state="cancelled"
                    )
                Conversation.objects.filter(
                    pk=run.conversation_id, active_run__uuid=run.uuid
                ).update(active_run=None)
            count += 1
    return count
