"""Persistência separada do pensamento (M2): protocolo vs projeção.

- PROTOCOLO (log append-only): segmentos brutos + signatures, redacted flags.
  Nunca exibido; serve p/ replay/continuação e auditoria. Vai em
  `run.snapshot["thinking_stream"]` (chave SNAPSHOT_KEY) — nenhum campo novo
  no banco, nenhuma migração, nenhum toque em `models.py`.
- PROJEÇÃO (painel "Resumo do pensamento"): texto resumido + estado, derivado
  do protocolo via `project_panel()`. O frontend renderiza SÓ a projeção.

Tudo puro (dicts), sem import Django: testável sem banco, reutilizável tanto
no caminho textual quanto no tool path de `generation.py`.
"""

from __future__ import annotations

SNAPSHOT_KEY = "thinking_stream"
PROTOCOL_VERSION = "thinking-stream-v1"

STATE_STREAMING = "streaming"
STATE_DONE = "done"
STATE_REDACTED = "redacted"
STATE_ABSENT = "absent"  # pensamento desligado/desconhecido: nada a mostrar

MAX_SUMMARY_CHARS = 4000


def new_log(*, display: str = "omitted") -> dict:
    return {
        "version": PROTOCOL_VERSION,
        "display": display,
        "segments": [],  # [{"thinking": str, "signature": str}]
        "redacted": False,
        "redact_reason": "",
        "completed": False,
    }


def append_delta(log: dict, *, thinking: str = "", signature: str = "") -> dict:
    """Acrescenta um fragmento; segmentos vazios são ignorados (sem ruído)."""
    if thinking or signature:
        log["segments"].append({"thinking": thinking or "", "signature": signature or ""})
    return log


def mark_redacted(log: dict, *, reason: str = "") -> dict:
    log["redacted"] = True
    log["redact_reason"] = reason or ""
    return log


def complete(log: dict) -> dict:
    log["completed"] = True
    return log


def protocol_text(log: dict) -> str:
    return "".join(s.get("thinking", "") for s in log.get("segments", []))


def has_signature(log: dict) -> bool:
    return any(s.get("signature") for s in log.get("segments", []))


def redact_for_display(text: str, *, limit: int = MAX_SUMMARY_CHARS) -> str:
    """Projeção segura: só o resumo, truncado com marcador explícito."""
    text = text or ""
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def project_panel(log: dict | None) -> dict:
    """Deriva o painel "Resumo do pensamento" — única fonte da UI.

    Nunca expõe signatures nem segmentos brutos; redacted vira aviso.
    """
    if not log:
        return {"state": STATE_ABSENT, "summary": "", "display": "omitted"}
    if log.get("redacted"):
        return {
            "state": STATE_REDACTED,
            "summary": "",
            "display": log.get("display", "omitted"),
            "reason": log.get("redact_reason", ""),
        }
    text = protocol_text(log)
    state = STATE_DONE if log.get("completed") else STATE_STREAMING
    if not text:
        state = STATE_ABSENT if log.get("completed") else STATE_STREAMING
    return {
        "state": state,
        "summary": redact_for_display(text),
        "display": log.get("display", "omitted"),
    }


def to_snapshot(snapshot: dict, log: dict) -> dict:
    """Grava o protocolo no snapshot da run (mutação rasa, retorna o dict)."""
    snapshot[SNAPSHOT_KEY] = {
        "version": log.get("version", PROTOCOL_VERSION),
        "display": log.get("display", "omitted"),
        "segments": [dict(s) for s in log.get("segments", [])],
        "redacted": bool(log.get("redacted", False)),
        "redact_reason": log.get("redact_reason", ""),
        "completed": bool(log.get("completed", False)),
    }
    return snapshot


def from_snapshot(snapshot: dict) -> dict | None:
    """Lê o protocolo do snapshot; None quando a run não tem pensamento."""
    raw = (snapshot or {}).get(SNAPSHOT_KEY)
    if not raw:
        return None
    log = new_log(display=raw.get("display", "omitted"))
    log["segments"] = [dict(s) for s in raw.get("segments", [])]
    log["redacted"] = bool(raw.get("redacted", False))
    log["redact_reason"] = raw.get("redact_reason", "")
    log["completed"] = bool(raw.get("completed", False))
    return log
