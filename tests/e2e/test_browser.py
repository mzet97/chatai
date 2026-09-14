"""Teste de NAVEGADOR real (Chromium headless) para os fluxos críticos.

Sobe uvicorn de verdade contra um banco temporário (CHAT_DB_PATH) e dirige
login → chat → envio (sem chave: estado de erro exibido) → configurações.
Sem chave e sem rede externa. Pula se o Playwright não estiver instalado.
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
def browser_server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("browser")
    db = str(tmp / "browser.sqlite3")
    empty_env = tmp / "empty.env"
    empty_env.write_text("DJANGO_SECRET_KEY=browser-test-only\n")
    # Banco + dotenv isolados: nenhuma chave real vaza para o teste, nenhuma
    # chamada de rede acontece (diagnóstico sem chave falha no passo 1, local).
    env = dict(os.environ, CHAT_DB_PATH=db, CHAT_DOTENV_PATH=str(empty_env))
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
            "get_user_model().objects.create_user('navegador', password='pw123456')",
        ],
        cwd=str(ROOT),
        env=env,
        check=True,
        capture_output=True,
    )
    port = 8137
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
            raise RuntimeError("uvicorn não subiu para o teste de navegador")
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def test_browser_critical_flows(browser_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            # Login.
            page.goto(f"{browser_server}/login/")
            page.fill("#id_username", "navegador")
            page.fill("#id_password", "pw123456")
            page.click("button[type=submit]")
            page.wait_for_url("**/", timeout=10000)
            assert page.locator("#sidebar").is_visible()
            assert page.locator("#composer").is_visible()

            # Novo chat + envio sem chave → erro exibido, sem travar a UI.
            page.fill("#input", "Olá, teste de navegador")
            page.click("#btn-send")
            page.wait_for_selector(".msg-user", timeout=10000)
            assert "Olá, teste de navegador" in page.locator(".msg-user").inner_text()
            page.wait_for_function(
                "() => document.getElementById('status').textContent.length > 0", timeout=15000
            )
            status = page.locator("#status").inner_text()
            assert status  # estado de erro/aviso visível (sem chave configurada)
            conv_url = page.url  # /c/<uuid>/ criada pelo envio
            assert "/c/" in conv_url

            # Configurações: efetiva renderizada, sem segredo no HTML.
            page.goto(f"{browser_server}/settings/")
            page.wait_for_selector("#effective", timeout=10000)
            html = page.content()
            assert "sk-ant" not in html
            assert "origem" in page.locator("#effective").inner_text().lower()

            # Switch Streaming: rótulo, estado padrão, teclado, persistência e rascunho.
            page.goto(conv_url)  # volta à conversa criada pelo envio acima
            page.wait_for_selector("#stream-toggle", timeout=10000)
            assert page.locator("#stream-wrap").inner_text().strip().startswith("Streaming")
            assert page.locator("#stream-toggle").is_checked()  # novas conversas: ativado
            page.fill("#input", "rascunho-preservado")
            page.locator("#stream-toggle").focus()
            page.keyboard.press("Space")  # alterna por teclado
            page.wait_for_function(
                "() => document.getElementById('status').textContent.includes('desligado')",
                timeout=10000,
            )
            assert not page.locator("#stream-toggle").is_checked()
            assert page.locator("#input").input_value() == "rascunho-preservado"
            page.reload()
            # O HTML estático nasce marcado; só vale após loadConversation aplicar
            # o estado do backend (título é preenchido na mesma leva, antes).
            page.wait_for_function(
                "() => document.getElementById('chat-title').textContent !== 'Nova conversa'",
                timeout=10000,
            )
            assert not page.locator("#stream-toggle").is_checked()  # veio do backend
            # Mobile: switch do header some, item do menu ⋯ assume.
            page.set_viewport_size({"width": 390, "height": 844})
            assert not page.locator("#stream-wrap").is_visible()
            page.click("#actions-btn")
            assert page.locator("#stream-menuitem").is_visible()
            assert "desativado" in page.locator("#stream-menuitem").inner_text()
            page.click("#stream-menuitem")
            page.wait_for_function(
                "() => document.getElementById('status').textContent.includes('ativado')",
                timeout=10000,
            )
            page.set_viewport_size({"width": 1280, "height": 800})

            # Diagnóstico sem chave: passo de config falha com dignidade.
            page.goto(f"{browser_server}/settings/")
            page.click("#d-run")
        finally:
            browser.close()
