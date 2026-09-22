"""M3: ensaio de migração — backup → destino separado → verificação.

Sem rede, sem Postgres/S3 aqui: prova o procedimento (integridade,
contagens, hashes, manifesto de arquivos) no SQLite local. As diferenças
SQLite/PostgreSQL e o backend S3 estão documentados em
docs/architecture-v2/migration.md; o ensaio real no homelab exige o
preflight M6. Sem commit.
"""

import hashlib
import json
import sqlite3

import pytest
from django.core.management import call_command

from chat.models import Conversation, Message

pytestmark = pytest.mark.django_db(transaction=True)


def _counts():
    from django.apps import apps

    out = {}
    for model in apps.get_models():
        out[model._meta.label] = model.objects.count()
    return out


def test_backup_restore_rehearsal_to_separate_target(tmp_path):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user("mig", password="pw")
    conv = Conversation.objects.create(owner=user, title="ensaio")
    Message.objects.create(conversation=conv, seq=1, role="user", text="olá?")
    before = _counts()
    sample = Message.objects.values_list("text", flat=True).first()

    backup = tmp_path / "backup.sqlite3"
    call_command("backup_db", out=str(backup))
    assert backup.exists()

    with sqlite3.connect(f"file:{backup}?mode=ro", uri=True) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        tables = {
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert "chat_conversation" in tables and "chat_message" in tables

    # Destino separado: anexa o backup e compara contagem + conteúdo.
    with sqlite3.connect(":memory:") as mem:
        mem.execute(f"ATTACH DATABASE '{backup}' AS src")
        n = mem.execute("SELECT COUNT(*) FROM src.chat_message").fetchone()[0]
        assert n == before["chat.Message"]
        text = mem.execute("SELECT text FROM src.chat_message LIMIT 1").fetchone()[0]
        assert text == sample


def test_files_manifest_copy_and_verify(tmp_path):
    from chat.services.storage import LocalObjectStore

    src = LocalObjectStore(root=tmp_path / "origem")
    meta = src.put("u1/base/doc.txt", b"binario-ficticio")
    manifest = {
        "files": [{"key": "u1/base/doc.txt", **meta}],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    dst = LocalObjectStore(root=tmp_path / "destino")
    loaded = json.loads(manifest_path.read_text())
    for item in loaded["files"]:
        payload = src.get(item["key"])
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]
        got = dst.put(item["key"], payload)
        assert got == {"sha256": item["sha256"], "size": item["size"]}
    assert dst.get("u1/base/doc.txt") == b"binario-ficticio"


def test_postgres_engine_fails_with_useful_message(monkeypatch):
    from config import settings as s

    monkeypatch.setenv("DB_ENGINE", "postgres")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "teste-fechado-0123456789abcdef")
    monkeypatch.setenv("DJANGO_DEBUG", "false")
    with pytest.raises(RuntimeError, match="psycopg|PGHOST|dedicado"):
        s._databases()
    monkeypatch.setenv("DB_ENGINE", "oracle-x")
    with pytest.raises(RuntimeError, match="desconhecido"):
        s._databases()
    monkeypatch.delenv("DB_ENGINE", raising=False)
    assert s._databases()["default"]["ENGINE"] == "django.db.backends.sqlite3"


def test_postgres_recusa_segredo_padrao_e_debug(monkeypatch):
    from config import settings as s

    monkeypatch.setenv("DB_ENGINE", "postgres")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "django-insecure-local-dev-only-change-me")
    monkeypatch.setenv("DJANGO_DEBUG", "false")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        s._databases()
    monkeypatch.setenv("DJANGO_SECRET_KEY", "teste-fechado-0123456789abcdef")
    monkeypatch.setenv("DJANGO_DEBUG", "true")
    with pytest.raises(RuntimeError, match="DJANGO_DEBUG=false"):
        s._databases()
