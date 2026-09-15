"""M4: Streamable HTTP contra o demo real em subprocesso (sem rede externa)."""

import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from chat.services.tools import mcp_client, ssrf

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVER = os.path.join(ROOT, "mcp_servers", "study_lessons", "server.py")


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def http_server():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, SERVER, "--http", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    for _ in range(100):
        try:
            urllib.request.urlopen(url, timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(timeout=10)


async def test_http_descoberta_e_chamada(http_server):
    cfg = mcp_client.HttpConfig(url=http_server, allow_loopback_http=True)
    info = await mcp_client.list_tools_http(cfg)
    assert info.protocol_version
    assert {"list_lessons", "read_lesson"} <= {t["name"] for t in info.tools}
    out = await mcp_client.call_tool_http(cfg, "read_lesson", {"lesson_id": "sse"})
    assert out["ok"] is True and "SSE" in out["text"]
    bad = await mcp_client.call_tool_http(cfg, "read_lesson", {"lesson_id": "nope"})
    assert bad["ok"] is False


async def test_http_sem_flag_demo_rejeita(http_server):
    cfg = mcp_client.HttpConfig(url=http_server, allow_loopback_http=False)
    with pytest.raises(ssrf.Rejected):
        await mcp_client.list_tools_http(cfg)
