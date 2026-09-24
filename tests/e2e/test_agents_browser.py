"""M5: fluxo de agentes no NAVEGADOR real (Chromium headless, stack real).

Página /agents/ → Criar agentes de exemplo (idempotente: 2 cliques, 4 perfis)
→ nova conversa via API → seletor Equipe + perfil Coordenador (UI de verdade)
→ aviso de Equipe visível → reload persiste modo e perfil → volta p/ Chat,
aviso some.

Sem chave e sem rede externa; nenhuma mensagem é enviada (sem custo).
Pula sem Playwright/navegadores.
"""

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
playwright = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="module")
def agents_server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("agents-browser")
    env = dict(
        os.environ,
        CHAT_DB_PATH=str(tmp / "agents.sqlite3"),
        CHAT_DOTENV_PATH=str(_empty_env(tmp)),
    )
    subprocess.run(
        [sys.executable, "manage.py", "migrate", "--noinput"],
        cwd=str(ROOT),
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import django,os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');"
            "django.setup();"
            "from django.contrib.auth import get_user_model;"
            "get_user_model().objects.create_user('agentes', password='pw123456')",
        ],
        cwd=str(ROOT),
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [sys.executable, "manage.py", "collectstatic", "--noinput"],
        cwd=str(ROOT),
        env=env,
        check=True,
        capture_output=True,
    )
    port = 8141
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "config.asgi:application",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/login/", timeout=3) as r:
                    assert r.status == 200
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise RuntimeError("uvicorn não subiu para o teste de agentes")
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def _empty_env(tmp):
    p = tmp / "empty.env"
    p.write_text("DJANGO_SECRET_KEY=agents-test-only\n")
    return p


def test_exemplos_seletor_equipe_aviso_persiste(agents_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"navegador indisponível: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        problems = []
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.on(
            "console",
            lambda m: problems.append(f"console: {m.text}") if m.type == "error" else None,
        )
        try:
            page.goto(f"{agents_server}/login/")
            page.fill("#id_username", "agentes")
            page.fill("#id_password", "pw123456")
            page.click("button[type=submit]")
            page.wait_for_url("**/", timeout=10000)

            # Página Agentes: exemplos idempotentes (2 cliques → 4 perfis).
            page.goto(f"{agents_server}/agents/")
            page.wait_for_selector("#ag-examples", timeout=10000)
            page.click("#ag-examples")
            page.wait_for_function(
                "() => document.querySelectorAll('#ag-list li').length === 4",
                timeout=45000,
            )
            page.click("#ag-examples")
            page.wait_for_timeout(1500)
            assert page.evaluate("() => document.querySelectorAll('#ag-list li').length") == 4
            names = page.locator("#ag-list").inner_text()
            assert "Coordenador" in names

            # Nova conversa + uuid do Coordenador via API (mesmo cookie/CSRF da página).
            ids = page.evaluate(
                """async () => {
                  const m = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
                  const h = {"Content-Type": "application/json",
                             "X-CSRFToken": decodeURIComponent(m[1])};
                  const c = await (await fetch("/api/conversations", {
                    method: "POST", headers: h,
                    body: JSON.stringify({title: "Equipe E2E"})})).json();
                  const a = await (await fetch("/api/agents")).json();
                  const coord = a.results.find((p) => p.kind === "coordinator");
                  return {conv: c.uuid, coord: coord.uuid};
                }"""
            )
            assert ids["conv"] and ids["coord"]

            # Seletor real: perfil + modo Equipe → aviso visível.
            page.goto(f"{agents_server}/c/{ids['conv']}/")
            page.evaluate("() => document.body.classList.add('head-expanded')")
            page.wait_for_selector("#agent-mode", state="visible", timeout=10000)
            page.wait_for_function(
                "() => !document.getElementById('agent-mode').disabled",
                timeout=15000,
            )
            page.locator("#agent-select").select_option(ids["coord"], force=True)
            page.locator("#agent-mode").select_option("team", force=True)
            page.wait_for_function(
                "() => !document.getElementById('team-banner') "
                "|| !document.getElementById('team-banner').hidden",
                timeout=45000,
            )
            assert page.locator("#team-banner").is_visible()
            assert "até 2 especialistas" in page.locator("#team-banner").inner_text()

            # Reload: modo e perfil persistem, aviso continua.
            page.reload()
            page.wait_for_function(
                "() => !document.getElementById('agent-mode').disabled "
                "&& document.getElementById('agent-mode').value === 'team'",
                timeout=15000,
            )
            assert page.locator("#team-banner").is_visible()

            # Volta p/ Chat: aviso some.
            page.locator("#agent-mode").select_option("chat", force=True)
            page.wait_for_function(
                "() => document.getElementById('team-banner').hidden",
                timeout=15000,
            )
            assert not [m for m in problems if "pageerror" in m], problems
        finally:
            browser.close()
