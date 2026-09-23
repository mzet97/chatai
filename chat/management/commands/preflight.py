"""M6: preflight somente-leitura (§26). Nunca revela Secrets.

Cada check: {name, status, detail}. `unknown` bloqueia só o passo
dependente. Saída humana ou `--format json`.
"""

import importlib

from django.core.management.base import BaseCommand


def _check_runtime():
    import sys

    import django

    try:
        anthropic = importlib.import_module("anthropic")
        sdk = getattr(anthropic, "__version__", "instalado")
    except ImportError:
        return {"name": "runtime", "status": "degraded", "detail": "pacote 'anthropic' ausente"}
    return {
        "name": "runtime",
        "status": "ok",
        "detail": f"python {sys.version.split()[0]}, "
        f"django {django.get_version()}, anthropic-sdk {sdk}",
    }


def _check_database():
    from django.conf import settings
    from django.db import connection

    engine = settings.DATABASES["default"]["ENGINE"]
    try:
        connection.ensure_connection()
    except Exception as exc:
        return {
            "name": "database",
            "status": "degraded",
            "detail": f"{engine}: sem conexão ({type(exc).__name__})",
        }
    return {"name": "database", "status": "ok", "detail": engine}


def _check_migrations():
    from django.db import connection
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(connection, ignore_no_migrations=True)
    missing = sorted(
        f"{app}.{name}"
        for app, name in loader.graph.nodes
        if (app, name) not in loader.applied_migrations
    )
    if missing:
        return {
            "name": "migrations",
            "status": "degraded",
            "detail": f"não aplicadas: {', '.join(missing[:5])}",
        }
    return {"name": "migrations", "status": "ok", "detail": "todas aplicadas"}


def _check_anthropic_key():
    import os

    present = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return {
        "name": "anthropic_key",
        "status": "ok" if present else "degraded",
        "detail": "configurada"
        if present
        else (
            "ausente: UI/uploads/indexação funcionam; geração real informa "
            "a falta sem trocar de provedor"
        ),
    }


def _check_oidc():
    from chat.services.identity import oidc_status

    info = oidc_status()
    return {"name": "oidc", **info}


def _check_storage():
    from django.conf import settings

    root = settings.RAG_STORAGE_DIR
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".preflight-write"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        return {
            "name": "storage",
            "status": "degraded",
            "detail": f"{root}: sem escrita ({exc.strerror})",
        }
    return {"name": "storage", "status": "ok", "detail": str(root)}


class Command(BaseCommand):
    help = "Preflight somente-leitura: ambiente, banco, chave, OIDC, storage."

    def add_arguments(self, parser):
        parser.add_argument("--format", default="text", choices=("text", "json"))
        parser.add_argument(
            "--cluster",
            action="store_true",
            help="Inclui checagens do cluster k3s (kubectl get|version, só leitura).",
        )

    def handle(self, *args, **options):
        checks = [
            _check_runtime(),
            _check_database(),
            _check_migrations(),
            _check_anthropic_key(),
            _check_oidc(),
            _check_storage(),
        ]
        if options["cluster"]:
            from chat.services.cluster_preflight import cluster_checks

            checks.extend(cluster_checks())
        if options["format"] == "json":
            import json

            self.stdout.write(json.dumps({"checks": checks}, ensure_ascii=False))
            return
        for check in checks:
            self.stdout.write(f"[{check['status']}] {check['name']}: {check['detail']}")
