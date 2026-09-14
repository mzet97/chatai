"""Critério 18: HTML+CSS+JS servidos pelo comando ASGI documentado (uvicorn real)."""

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.integration
def test_uvicorn_serves_app_and_static(tmp_path):
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "config.asgi:application",
            "--host",
            "127.0.0.1",
            "--port",
            "8129",
        ],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.time() + 25
        body = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:8129/login/", timeout=3) as r:
                    assert r.status == 200
                    body = r.read().decode()
                break
            except OSError:
                time.sleep(0.5)
        assert body is not None and "claude-chat-local" in body
        for path in (
            "static/chat/css/app.css",
            "static/chat/js/chat.js",
            "static/chat/js/sse.js",
            "static/chat/js/api.js",
            "static/chat/js/markdown.js",
            "static/chat/js/settings-page.js",
        ):
            with urllib.request.urlopen(f"http://127.0.0.1:8129/{path}", timeout=5) as r:
                assert r.status == 200, path
                assert len(r.read()) > 100, path
    finally:
        proc.terminate()
        proc.wait(timeout=15)
