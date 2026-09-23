"""Heartbeat do worker de ingestão (arquivo, sem migração nem dependência).

O `rag_worker` grava a cada passada; a view lê o mtime. Sem batimento
recente, a UI mostra "Processador de documentos offline" — a fila segue
visível, sem fingir processamento.
"""

from __future__ import annotations

import json
import time

STALE_AFTER_SECONDS = 90


def _path():
    from chat.services.rag.storage import rag_root

    return rag_root() / ".worker_heartbeat"


def beat(worker_id: str) -> None:
    try:
        _path().write_text(json.dumps({"worker_id": worker_id, "ts": time.time()}))
    except OSError:
        pass


def status() -> dict:
    try:
        raw = json.loads(_path().read_text())
        age = time.time() - float(raw.get("ts", 0))
    except (OSError, ValueError, TypeError, KeyError):
        return {"online": False, "last_seen": None, "worker_id": None}
    if age > STALE_AFTER_SECONDS:
        return {"online": False, "last_seen": raw.get("ts"), "worker_id": raw.get("worker_id")}
    return {"online": True, "last_seen": raw.get("ts"), "worker_id": raw.get("worker_id")}
