"""M6: checagens de cluster somente-leitura (§26, passo 2 da retomada).

Só executa `kubectl get|version` (nunca `apply/delete/logs/secrets`);
só conta estados (Ready, readyReplicas, Programmed) — nunca imprime
objetos íntegros, nunca lê Secrets. Opt-in via `preflight --cluster`:
sem kubeconfig ou sem kubectl, cada check vira `degraded`/`unknown`
com motivo, nunca exceção.
"""

from __future__ import annotations

import json
import shutil
import subprocess

TIMEOUT_S = 15

# (namespace, kind, name) verificados como prontos no perfil homelab.
EXPECTED_WORKLOADS: tuple[tuple[str, str, str], ...] = (
    ("rabbitmq", "statefulset", "rabbitmq"),
    ("redis", "statefulset", "redis-master"),
    ("minio", "statefulset", "minio"),
    ("elastic", "statefulset", "elastic-es-default"),
    ("monitoring", "statefulset", "loki"),
)

GATEWAY_NAMESPACE = "kube-system"
GATEWAY_NAME = "homelab-gateway"


def _run_kubectl(*args: str) -> tuple[bool, str]:
    """Roda `kubectl`_allowlist (get|version). Retorna (ok, stdout|motivo)."""
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        return False, "kubectl ausente no PATH"
    if not args or args[0] not in ("get", "version"):
        return False, "verbo fora da allowlist (só get|version)"
    try:
        proc = subprocess.run(
            [kubectl, *args],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout após {TIMEOUT_S}s"
    except OSError as exc:
        return False, f"falha ao executar ({type(exc).__name__})"
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        return False, (err[-1][:160] if err else f"saída {proc.returncode}")
    return True, proc.stdout


def check_cluster_api() -> dict:
    ok, out = _run_kubectl("version", "-o", "json")
    if not ok:
        return {"name": "cluster_api", "status": "degraded", "detail": out}
    try:
        server = json.loads(out).get("serverVersion", {}).get("gitVersion", "?")
    except (ValueError, AttributeError):
        return {
            "name": "cluster_api",
            "status": "unknown",
            "detail": "resposta do servidor ilegível",
        }
    return {"name": "cluster_api", "status": "ok", "detail": f"server {server}"}


def check_cluster_nodes() -> dict:
    ok, out = _run_kubectl("get", "nodes", "-o", "json")
    if not ok:
        return {"name": "cluster_nodes", "status": "degraded", "detail": out}
    try:
        items = json.loads(out).get("items", [])
    except ValueError:
        return {
            "name": "cluster_nodes",
            "status": "unknown",
            "detail": "lista de nós ilegível",
        }
    not_ready = []
    pressured = []
    for node in items:
        name = node.get("metadata", {}).get("name", "?")
        conds = {
            c.get("type"): c.get("status") for c in node.get("status", {}).get("conditions", [])
        }
        if conds.get("Ready") != "True":
            not_ready.append(name)
        if conds.get("MemoryPressure") == "True":
            pressured.append(name)
    if not_ready:
        return {
            "name": "cluster_nodes",
            "status": "degraded",
            "detail": f"nós não Ready: {', '.join(not_ready[:3])}",
        }
    if pressured:
        return {
            "name": "cluster_nodes",
            "status": "degraded",
            "detail": f"MemoryPressure em: {', '.join(pressured[:3])}",
        }
    return {
        "name": "cluster_nodes",
        "status": "ok",
        "detail": f"{len(items)} nó(s) Ready, sem MemoryPressure",
    }


def check_cluster_workloads() -> dict:
    missing: list[str] = []
    unready: list[str] = []
    for namespace, kind, name in EXPECTED_WORKLOADS:
        ok, out = _run_kubectl(
            "get",
            kind,
            name,
            "-n",
            namespace,
            "-o",
            "jsonpath={.status.readyReplicas}/{.spec.replicas}",
        )
        if not ok:
            missing.append(f"{namespace}/{name}")
        elif not _all_ready(out):
            unready.append(f"{namespace}/{name}")
    if missing:
        return {
            "name": "cluster_workloads",
            "status": "degraded",
            "detail": f"não encontrados: {', '.join(missing[:4])}",
        }
    if unready:
        return {
            "name": "cluster_workloads",
            "status": "degraded",
            "detail": f"não prontos: {', '.join(unready[:4])}",
        }
    return {
        "name": "cluster_workloads",
        "status": "ok",
        "detail": f"{len(EXPECTED_WORKLOADS)} workloads prontos",
    }


def _all_ready(frac: str) -> bool:
    try:
        ready, want = frac.strip().split("/")
        return int(ready) >= int(want) > 0
    except (ValueError, AttributeError):
        return False


def check_cluster_gateway() -> dict:
    ok, out = _run_kubectl(
        "get",
        "gateway",
        GATEWAY_NAME,
        "-n",
        GATEWAY_NAMESPACE,
        "-o",
        "jsonpath={.status.conditions[?(@.type=='Programmed')].status}",
    )
    if not ok:
        return {"name": "cluster_gateway", "status": "degraded", "detail": out}
    if out.strip() == "True":
        return {
            "name": "cluster_gateway",
            "status": "ok",
            "detail": f"{GATEWAY_NAMESPACE}/{GATEWAY_NAME} Programmed",
        }
    return {
        "name": "cluster_gateway",
        "status": "unknown",
        "detail": f"{GATEWAY_NAMESPACE}/{GATEWAY_NAME} sem Programmed=True",
    }


def cluster_checks() -> list[dict]:
    """Ordem fixa; cada check nunca levanta exceção para o chamador."""
    checks = []
    for fn in (
        check_cluster_api,
        check_cluster_nodes,
        check_cluster_workloads,
        check_cluster_gateway,
    ):
        try:
            checks.append(fn())
        except Exception as exc:  # rede/kubeconfig quebrados não derrubam o preflight
            checks.append(
                {
                    "name": fn.__name__.replace("check_", ""),
                    "status": "unknown",
                    "detail": f"falha interna contida ({type(exc).__name__})",
                }
            )
    return checks
