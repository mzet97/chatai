"""Discovery MCP: conexão demo real → snapshots consultáveis no catálogo."""

import os
import sys

import pytest

from chat.models_tools import MCPConnection, ToolCatalogSnapshot
from chat.services.tools import discovery

pytestmark = pytest.mark.django_db(transaction=True)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def conn(db, django_user_model):
    owner = django_user_model.objects.create_user("mcpowner", password="x")
    return MCPConnection.objects.create(
        owner=owner,
        alias="study-lessons",
        transport="stdio",
        state="active",
        config={
            "command": sys.executable,
            "args": [os.path.join(ROOT, "mcp_servers", "study_lessons", "server.py")],
        },
    )


def test_refresh_cria_snapshots_demo(conn):
    out = discovery.refresh_connection(conn.pk)
    assert out["tools"] == 2
    assert out["protocol_version"]
    snaps = ToolCatalogSnapshot.objects.filter(connection=conn, revision=conn.revision)
    assert {s.original_name for s in snaps} == {"list_lessons", "read_lesson"}
    assert all(s.anthropic_name and s.schema_hash for s in snaps)
    conn.refresh_from_db()
    assert conn.last_error == "" and conn.last_checked_at is not None


def test_refresh_reatualiza_sem_duplicar(conn):
    discovery.refresh_connection(conn.pk)
    discovery.refresh_connection(conn.pk)
    assert ToolCatalogSnapshot.objects.filter(connection=conn).count() == 2


def test_refresh_falha_registra_erro(conn):
    conn.config = {"command": "/nao/existe"}
    conn.save()
    with pytest.raises(RuntimeError):
        discovery.refresh_connection(conn.pk)
    conn.refresh_from_db()
    assert conn.last_error != ""
    assert ToolCatalogSnapshot.objects.filter(connection=conn).count() == 0
