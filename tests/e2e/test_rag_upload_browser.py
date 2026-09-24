"""Aceite do upload integrado (navegador real, stack documentada).

Conta comum vazia → Conhecimento → Enviar documentos → cria base inline →
envia Aurora.txt pela UI → worker real publica → "Pronto para consulta" →
reload persiste → Fontes → Enviar documentos (rascunho preservado) →
seleciona base → evidência recuperada com fonte kb://.

Ingestão real (embeddings locais preparados); geração via SDK simulado é
coberta pelos testes M5 — aqui o payload de evidências é verificado no
servidor. Pula sem Playwright/navegadores.
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
AURORA = "No projeto fictício Aurora, a revisão interna ocorre às quintas-feiras, às 14h.\n"


@pytest.fixture(scope="module")
def upload_server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rag-upload")
    env = dict(
        os.environ,
        CHAT_DB_PATH=str(tmp / "upload.sqlite3"),
        CHAT_RAG_DIR=str(tmp / "rag"),
        CHAT_DOTENV_PATH=str(_empty_env(tmp)),
    )
    subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"],
                   cwd=str(ROOT), env=env, check=True, capture_output=True)
    subprocess.run(
        [sys.executable, "-c",
         "import django,os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');"
         "django.setup();"
         "from django.contrib.auth import get_user_model;"
         "get_user_model().objects.create_user('comum', password='pw123456')"],
        cwd=str(ROOT), env=env, check=True, capture_output=True,
    )
    subprocess.run([sys.executable, "manage.py", "collectstatic", "--noinput"],
                   cwd=str(ROOT), env=env, check=True, capture_output=True)
    port = 8138
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "config.asgi:application",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
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
            raise RuntimeError("uvicorn não subiu para o aceite de upload")
        yield f"http://127.0.0.1:{port}", env
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def _empty_env(tmp):
    p = tmp / "empty.env"
    p.write_text("DJANGO_SECRET_KEY=upload-test-only\n")
    return p


def test_upload_publica_e_recupera_na_conversa(upload_server, tmp_path):
    from playwright.sync_api import sync_playwright

    base_url, env = upload_server
    aurora = tmp_path / "aurora.txt"
    aurora.write_text(AURORA, encoding="utf-8")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"navegador indisponível: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        problems = []
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.on("console", lambda m: problems.append(f"console: {m.text}")
                if m.type == "error" else None)
        try:
            page.goto(f"{base_url}/login/")
            page.fill("#id_username", "comum")
            page.fill("#id_password", "pw123456")
            page.click("button[type=submit]")
            page.wait_for_url("**/", timeout=10000)

            # Entrada principal, visível no estado vazio.
            page.goto(f"{base_url}/knowledge/")
            page.wait_for_selector("#kb-upload-open", timeout=10000)
            page.click("#kb-upload-open")
            page.wait_for_selector(".upload-dialog", timeout=10000)
            assert "Enviar documentos" in page.locator(".upload-dialog h2").inner_text()

            # Cria base sem sair do diálogo.
            page.fill("#up-newbase", "Aurora")
            page.click("text=Criar base")
            page.wait_for_function(
                "() => [...document.querySelectorAll('#up-base option')]"
                ".some(o => o.text === 'Aurora')",
                timeout=10000,
            )
            # Arquivo real pelo seletor + segundo via arrastar (drop real no zone).
            page.set_input_files(".upload-dialog input[type=file]", str(aurora))
            page.evaluate(
                "() => { const dt = new DataTransfer();"
                " dt.items.add(new File(['Aurora 2: o portão abre ao meio-dia.'],"
                " 'aurora2.txt', {type: 'text/plain'}));"
                " document.querySelector('.up-zone').dispatchEvent("
                " new DragEvent('drop', {bubbles: true, dataTransfer: dt})); }"
            )
            page.wait_for_function(
                "() => [...document.querySelectorAll('.up-file strong')]"
                ".some(s => s.textContent === 'aurora2.txt')",
                timeout=10000,
            )
            # Arquivo inválido: erro por item, sem bloquear os válidos.
            bad = tmp_path / "ruim.exe"
            bad.write_bytes(b"MZ" + b"\x00" * 20)
            page.set_input_files(
                ".upload-dialog input[type=file]", [str(aurora), str(bad)])
            page.wait_for_function(
                "() => [...document.querySelectorAll('.up-file .meta')]"
                ".some(s => s.textContent.includes('não aceito'))",
                timeout=10000,
            )
            assert not page.locator(".upload-dialog .btn.primary").is_disabled()
            page.click(".upload-dialog .btn.primary")  # Enviar e indexar
            page.wait_for_function(
                "() => [...document.querySelectorAll('.up-file .meta')]"
                ".some(s => s.textContent.includes('Recebido'))",
                timeout=30000,
            )
            # Worker parado até aqui: publica via comando real (loop; 2 arquivos).
            worker = subprocess.Popen(
                [sys.executable, "manage.py", "rag_worker"],
                cwd=str(ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            failed = None
            try:
                page.wait_for_function(
                    "() => [...document.querySelectorAll('.up-file .meta')]"
                    ".filter(s => s.textContent.includes('Pronto para consulta'))"
                    ".length === 2",
                    timeout=180000,
                )
            except Exception as wait_exc:
                failed = wait_exc
            if failed is not None:
                states = page.evaluate(
                    "() => [...document.querySelectorAll('.up-file')].map("
                    "li => li.querySelector('strong').textContent + ' :: '"
                    "+ li.querySelector('.meta').textContent)")
                worker.terminate()
                try:
                    out, _ = worker.communicate(timeout=20)
                    tail = out.decode()[-1500:]
                except Exception as comm_exc:
                    worker.kill()
                    tail = f"<sem saída: {comm_exc}>"
                raise AssertionError(
                    f"jobs não publicaram. linhas={states} worker={tail}") from failed
            try:
                worker.terminate()
                worker.wait(timeout=30)
            except Exception:
                worker.kill()
            page.keyboard.press("Escape")
            page.wait_for_selector(".upload-dialog", state="detached", timeout=5000)

            # Reload: reseleciona a base; a linha persiste com estado confirmado.
            page.reload()
            page.wait_for_selector("#kb-list .linklike", timeout=10000)
            page.locator("#kb-list .linklike", has_text="Aurora").click()
            page.wait_for_function(
                "() => [...document.querySelectorAll('#kb-docs .kb-doc-row')].some("
                "li => li.textContent.includes('aurora.txt') && li.textContent.includes('pronto'))",
                timeout=15000,
            )

            # Na conversa: cria uma via envio (sem chave: erro digno, mas /c/ nasce).
            page.goto(f"{base_url}/")
            page.wait_for_selector("#composer", timeout=10000)
            page.fill("#input", "pergunta sobre aurora")
            page.click("#btn-send")
            page.wait_for_url("**/c/**", timeout=15000)
            # Aguarda o fim do run (sem chave: falha rápida e digna).
            page.wait_for_selector("#btn-stop", state="hidden", timeout=60000)
            # Fontes → Enviar documentos, rascunho preservado.
            page.evaluate("() => document.body.classList.add('head-expanded')")
            page.wait_for_selector("#sources-btn", timeout=10000)
            page.fill("#input", "rascunho-aurora")
            page.click("#sources-btn")
            page.wait_for_selector("#sources-upload", timeout=10000)
            page.click("#sources-upload")
            page.wait_for_selector(".upload-dialog", timeout=10000)
            page.keyboard.press("Escape")
            page.wait_for_selector(".upload-dialog", state="detached", timeout=5000)
            assert page.locator("#input").input_value() == "rascunho-aurora"
            page.click("#sources-btn")
            page.wait_for_selector("#sources-list .tool-check", timeout=15000)
            page.locator("#sources-list .tool-check", has_text="Aurora").click()
            page.wait_for_function(
                "() => document.getElementById('sources-label').textContent.includes('(1)')",
                timeout=10000,
            )
            # Mobile: diálogo legível (popover segue aberto após a seleção).
            page.click("#sources-upload")
            page.wait_for_selector(".upload-dialog", timeout=10000)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.locator(".upload-dialog .btn.primary").is_visible()
            page.set_viewport_size({"width": 1280, "height": 900})

            assert problems == [], f"erros de console/página: {problems[:5]}"
        finally:
            browser.close()

    # Evidência recuperada com fonte kb:// (ingestão real, geração simulada nos M5).
    out = subprocess.run(
        [sys.executable, "-c",
         "import django,os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');"
         "django.setup();"
         "from django.contrib.auth import get_user_model;"
         "from chat.models_rag import KnowledgeBase;"
         "from chat.services.rag import retrieval;"
         "u = get_user_model().objects.get(username='comum');"
         "kb = KnowledgeBase.objects.get(owner=u, name='Aurora');"
         "res = retrieval.retrieve("
         "u.pk, [str(kb.uuid)], 'Quando ocorre a revisão do projeto Aurora?');"
         "ev = [e for e in res.evidences if 'quintas' in e.text];"
         "assert ev, 'fato Aurora não recuperado';"
         "assert ev[0].kb_id.startswith('kb://'), ev[0].kb_id;"
         "print('EVIDENCIA OK', ev[0].kb_id)"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300,
    )
    assert "EVIDENCIA OK kb://" in out.stdout, out.stderr[-2000:]
