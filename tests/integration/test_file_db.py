"""Critérios 1, 11, 12 em SQLite ARQUIVO (concorrência/restart de verdade)."""

import os
import threading
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from chat.models import Conversation, GenerationRun, Message
from chat.services.generation import RunBusy, reserve_run
from chat.services.recovery import recover_abandoned_runs

pytestmark = pytest.mark.django_db(transaction=True)


def _user(username="fileuser"):
    return get_user_model().objects.create_user(username, password="pw123456")


def test_restart_keeps_chats_and_recovery_marks_abandoned(file_db):
    """Critério 1/11: restart não perde chats; run órfã vira abandoned (nunca done)."""
    u = _user()
    conv = Conversation.objects.create(owner=u, title="persistente")
    Message.objects.create(conversation=conv, seq=1, role="user", text="oi", state="ok")

    from django.db import connections

    connections.close_all()  # simula restart: fecha e reabre o mesmo arquivo
    assert Conversation.objects.get(pk=conv.pk).title == "persistente"
    assert Message.objects.filter(conversation=conv).count() == 1

    stale = GenerationRun.objects.create(
        conversation=conv,
        user_message=Message.objects.get(conversation=conv, seq=1),
        idempotency_key="orphan",
        content_hash="h",
        state="streaming",
        worker_pid=999_999_999,
    )  # pid morto
    GenerationRun.objects.filter(pk=stale.pk).update(
        last_heartbeat=timezone.now() - timedelta(minutes=30)
    )
    conv.active_run = stale
    conv.save()
    assert recover_abandoned_runs() == 1
    stale.refresh_from_db()
    conv.refresh_from_db()
    assert stale.state == "abandoned" and conv.active_run is None


def test_live_run_never_marked_abandoned(file_db):
    """Recuperação não toca em execução realmente ativa (mesmo pid, heartbeat fresco)."""
    u = _user("liveuser")
    conv = Conversation.objects.create(owner=u, title="viva")
    msg = Message.objects.create(conversation=conv, seq=1, role="user", text="oi", state="ok")
    run = GenerationRun.objects.create(
        conversation=conv,
        user_message=msg,
        idempotency_key="live",
        content_hash="h",
        state="streaming",
        worker_pid=os.getpid(),
    )
    conv.active_run = run
    conv.save()
    assert recover_abandoned_runs() == 0
    run.refresh_from_db()
    assert run.state == "streaming"


def test_two_tabs_race_single_winner(file_db):
    """Critério 12: duas abas concorrentes → uma reserva, outra RunBusy (update atômico)."""
    u = _user("raceuser")
    conv = Conversation.objects.create(owner=u, title="corrida")
    outcomes = []

    def attempt(key):
        try:
            reserve_run(
                conversation=Conversation.objects.get(pk=conv.pk),
                content="mesma pergunta",
                idempotency_key=key,
            )
            outcomes.append((key, "won"))
        except RunBusy:
            outcomes.append((key, "busy"))
        except Exception as exc:  # noqa: BLE001 — registra qualquer outra falha
            outcomes.append((key, f"error:{exc}"))

    threads = [threading.Thread(target=attempt, args=(f"tab-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(v for _, v in outcomes) == ["busy", "won"]
    # A exclusividade falha DENTRO da transação → rollback total, sem linha órfã.
    assert GenerationRun.objects.filter(conversation=conv).count() == 1
