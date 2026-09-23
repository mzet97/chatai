"""Admin staff: MCP/RAG/ferramentas gerenciáveis; comum sem acesso."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from chat.models_tools import MCPConnection

pytestmark = pytest.mark.integration


@pytest.fixture
def staff_user(db):
    from django.contrib.auth.models import Permission

    u = get_user_model().objects.create_user("staff", password="pw123456")
    u.is_staff = True
    u.save()
    u.user_permissions.set(Permission.objects.all())
    return u


@pytest.fixture
def common_user(db):
    return get_user_model().objects.create_user("comum", password="pw123456")


def test_admin_login_e_mcp_crud(staff_user):
    c = Client()
    assert c.login(username="staff", password="pw123456")
    index = c.get("/admin/")
    assert index.status_code == 200
    html = index.content.decode()
    assert "chat/css/admin.css" in html
    assert "claude-chat-local" in html
    assert c.get("/admin/chat/mcpconnection/").status_code == 200
    r = c.post(
        "/admin/chat/mcpconnection/add/",
        {
            "alias": "demo",
            "owner": staff_user.pk,
            "transport": "streamable_http",
            "state": "disabled",
            "config": '{"url": "http://127.0.0.1:8139/mcp"}',
            "credential_ref": "",
            "granted_user_ids": "[]",
            "last_error": "",
        },
    )
    assert r.status_code in (200, 302)
    conn = MCPConnection.objects.get(alias="demo", owner=staff_user)
    assert conn.transport == "streamable_http"
    assert conn.state == "disabled"
    assert conn.config["url"] == "http://127.0.0.1:8139/mcp"


def test_admin_rag_e_tools_visiveis(staff_user):
    c = Client()
    assert c.login(username="staff", password="pw123456")
    for path in (
        "/admin/chat/knowledgebase/",
        "/admin/chat/document/",
        "/admin/chat/ingestionjob/",
        "/admin/chat/conversationtoolprefs/",
        "/admin/chat/toolapproval/",
        "/admin/chat/toolinvocation/",
        "/admin/chat/connectionsettings/",
    ):
        assert c.get(path).status_code == 200, path


def test_admin_bloqueado_para_comum(common_user):
    c = Client()
    assert c.login(username="comum", password="pw123456")
    r = c.get("/admin/chat/mcpconnection/")
    assert r.status_code in (302, 403)
