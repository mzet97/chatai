"""Critério 17: backup consistente (API backup) + restauração íntegra."""

import sqlite3

import pytest
from django.core.management import call_command

from chat.models import Conversation

pytestmark = pytest.mark.django_db(transaction=True)


def test_backup_and_restore_roundtrip(file_db, tmp_path):
    from django.contrib.auth import get_user_model

    owner = get_user_model().objects.create_user("bkp", password="pw123456")
    user_conv = Conversation.objects.create(owner=owner, title="para backup")
    out = str(tmp_path / "backup.sqlite3")
    call_command("backup_db", out=out)
    assert sqlite3.connect(out).execute("select count(*) from chat_conversation").fetchone()[0] == 1

    Conversation.objects.all().delete()
    assert Conversation.objects.count() == 0
    # Restauração troca o arquivo sob os pés: fecha conexões antes e depois
    # (em produção a aplicação está parada; aqui simulamos).
    from django.db import connections

    connections.close_all()
    call_command("restore_db", src=out)
    connections.close_all()
    assert Conversation.objects.get(pk=user_conv.pk).title == "para backup"
