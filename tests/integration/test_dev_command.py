"""Comando dev: servidor + worker juntos, com heartbeat verificável (§7)."""

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent.parent


def test_dev_sobe_servidor_e_worker(tmp_path):
    env = dict(
        os.environ,
        CHAT_DB_PATH=str(tmp_path / "dev.sqlite3"),
        CHAT_RAG_DIR=str(tmp_path / "rag"),
        CHAT_DOTENV_PATH=str(_empty_env(tmp_path)),
    )
    subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"],
                   cwd=str(ROOT), env=env, check=True, capture_output=True)
    proc = subprocess.Popen(
        [sys.executable, "manage.py", "dev", "--port", "8142"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:8142/login/", timeout=3) as r:
                    assert r.status == 200
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise AssertionError("dev: servidor não respondeu")
        beat = tmp_path / "rag" / ".worker_heartbeat"
        deadline = time.time() + 40
        while time.time() < deadline:
            if beat.exists():
                break
            time.sleep(0.5)
        else:
            raise AssertionError("dev: worker sem heartbeat")
    finally:
        proc.terminate()
        proc.wait(timeout=20)


def _empty_env(tmp_path):
    p = tmp_path / "empty.env"
    p.write_text("DJANGO_SECRET_KEY=dev-test-only\n")
    return p
