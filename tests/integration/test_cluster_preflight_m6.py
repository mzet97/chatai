"""M6: preflight --cluster (só leitura, kubectl get|version).

Tudo com subprocess simulado: a suíte nunca exige kubeconfig nem toca
o cluster. O contato real é opt-in fora da suíte (`preflight --cluster`).
"""

import io
import json
import subprocess

import pytest

from chat.services import cluster_preflight as cp

pytestmark = pytest.mark.django_db(transaction=False)


def _completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=["kubectl"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _patch_run(monkeypatch, handler, which="kubectl"):
    monkeypatch.setattr(cp.shutil, "which", lambda _: which)
    monkeypatch.setattr(cp.subprocess, "run", handler)


def test_sem_flag_sem_checks_cluster():
    from django.core.management import call_command

    buf = io.StringIO()
    call_command("preflight", format="json", stdout=buf)
    names = {c["name"] for c in json.loads(buf.getvalue())["checks"]}
    assert not names & {
        "cluster_api",
        "cluster_nodes",
        "cluster_workloads",
        "cluster_gateway",
    }


def test_kubectl_ausente_tudo_degraded(monkeypatch):
    from django.core.management import call_command

    _patch_run(monkeypatch, lambda *a, **k: _completed(), which=None)
    buf = io.StringIO()
    call_command("preflight", format="json", cluster=True, stdout=buf)
    data = json.loads(buf.getvalue())
    cluster = [c for c in data["checks"] if c["name"].startswith("cluster_")]
    assert {c["name"] for c in cluster} == {
        "cluster_api",
        "cluster_nodes",
        "cluster_workloads",
        "cluster_gateway",
    }
    assert {c["status"] for c in cluster} == {"degraded"}
    assert "SECRET" not in buf.getvalue() and "sk-ant" not in buf.getvalue()


def test_cluster_saudavel_mockado_ok(monkeypatch):
    from django.core.management import call_command

    nodes = {
        "items": [
            {
                "metadata": {"name": "k8s1"},
                "status": {
                    "conditions": [
                        {"type": "Ready", "status": "True"},
                        {"type": "MemoryPressure", "status": "False"},
                    ]
                },
            }
        ]
    }

    def handler(cmd, **kwargs):
        if cmd[1] == "version":
            return _completed(json.dumps({"serverVersion": {"gitVersion": "v1.36.4"}}))
        if cmd[1:3] == ["get", "nodes"]:
            return _completed(json.dumps(nodes))
        if "gateway" in cmd:
            return _completed("True")
        return _completed("1/1")

    _patch_run(monkeypatch, handler)
    buf = io.StringIO()
    call_command("preflight", format="json", cluster=True, stdout=buf)
    data = json.loads(buf.getvalue())
    cluster = {c["name"]: c for c in data["checks"] if c["name"].startswith("cluster_")}
    assert all(c["status"] == "ok" for c in cluster.values()), cluster
    assert "v1.36.4" in cluster["cluster_api"]["detail"]


def test_no_pronto_e_pressao_viram_degraded(monkeypatch):
    nodes = {
        "items": [
            {
                "metadata": {"name": "k8s1"},
                "status": {
                    "conditions": [
                        {"type": "Ready", "status": "True"},
                        {"type": "MemoryPressure", "status": "True"},
                    ]
                },
            }
        ]
    }

    def handler(cmd, **kwargs):
        if cmd[1:3] == ["get", "nodes"]:
            return _completed(json.dumps(nodes))
        return _completed("", returncode=1, stderr="Error: not found")

    _patch_run(monkeypatch, handler)
    assert cp.check_cluster_nodes()["status"] == "degraded"
    assert "MemoryPressure" in cp.check_cluster_nodes()["detail"]
    workloads = cp.check_cluster_workloads()
    assert workloads["status"] == "degraded"
    assert "não encontrados" in workloads["detail"]


def test_verbo_fora_da_allowlist_recusado(monkeypatch):
    _patch_run(monkeypatch, lambda *a, **k: _completed("segredo!"))
    ok, motivo = cp._run_kubectl("delete", "pod", "x")
    assert not ok and "allowlist" in motivo


def test_timeout_vira_motivo_sem_excecao(monkeypatch):
    def handler(*a, **k):
        raise subprocess.TimeoutExpired(cmd="kubectl", timeout=15)

    _patch_run(monkeypatch, handler)
    assert cp.check_cluster_api() == {
        "name": "cluster_api",
        "status": "degraded",
        "detail": "timeout após 15s",
    }
