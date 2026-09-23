"""Contexto protocolar versionado entre turnos (M4/TV-3.3).

Registro leve (só hashes, contagens e versões — nunca Base64, nunca
segredo) do prefixo que condiciona o raciocínio do modelo: system,
modelo, evidências RAG, ferramentas autorizadas e variantes de imagem.
Mudança de prefixo entre turnos → nova versão registrada + aviso de
reinício do raciocínio; nunca adivinhar protocolo nem conservar dado
revogado para satisfazer assinatura.
"""

from __future__ import annotations

import hashlib

PROTOCOL_VERSION = "imgproto-v1"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def system_hash(system: str) -> str:
    """Hash do system composto (base + adendo RAG)."""
    return _digest(system or "")


def image_variant_ids(entries: list[dict]) -> list[str]:
    """IDs estáveis das variantes (sha256 persistido; legado = hash do base64)."""
    ids = []
    for entry in entries or []:
        sha = entry.get("sha256")
        if not sha:
            sha = _digest(entry.get("data") or "")
        ids.append(sha)
    return ids


def prefix_hash(
    *,
    system: str,
    model: str,
    rag_status: str,
    rag_run_id: int | None,
    tools_enabled: list[str],
    image_ids: list[str],
) -> str:
    """Hash canônico do prefixo protocolar de um turno."""
    parts = [
        system_hash(system),
        (model or "").strip(),
        rag_status or "skipped",
        str(rag_run_id or ""),
        ",".join(sorted(tools_enabled or [])),
        ",".join(image_ids or []),
    ]
    return _digest("\x00".join(parts))


def build_record(
    *,
    system: str,
    model: str,
    rag_status: str,
    rag_run_id: int | None = None,
    tools_enabled: list[str] | None = None,
    image_entries: list[dict] | None = None,
) -> dict:
    """Registro do turno atual (sem versão — versionada em `track`)."""
    image_ids = image_variant_ids(image_entries)
    return {
        "version": PROTOCOL_VERSION,
        "prefix_hash": prefix_hash(
            system=system,
            model=model,
            rag_status=rag_status,
            rag_run_id=rag_run_id,
            tools_enabled=tools_enabled or [],
            image_ids=image_ids,
        ),
        "model": model,
        "system_hash": system_hash(system),
        "rag_status": rag_status,
        "rag_run_id": rag_run_id,
        "tools_enabled": sorted(tools_enabled or []),
        "image_variants": image_ids,
        "image_count": len(image_ids),
    }


def previous_record(snapshot: dict | None) -> dict | None:
    """Registro protocolar da execução anterior (None se ausente)."""
    if not isinstance(snapshot, dict):
        return None
    record = snapshot.get("protocol")
    return record if isinstance(record, dict) else None


def track(previous: dict | None, current: dict) -> dict:
    """Versiona o turno: mesma geração quando o prefixo é igual.

    Retorna o registro atual acrescido de `generation` (n) e
    `prefix_changed` (bool). Primeira execução: geração 1, sem mudança.
    """
    if previous is None or previous.get("version") != PROTOCOL_VERSION:
        return {**current, "generation": 1, "prefix_changed": False}
    changed = previous.get("prefix_hash") != current.get("prefix_hash")
    generation = previous.get("generation", 1)
    try:
        generation = int(generation)
    except (TypeError, ValueError):
        generation = 1
    return {
        **current,
        "generation": generation if not changed else generation + 1,
        "prefix_changed": changed,
    }


def restart_warning(record: dict) -> str | None:
    """Aviso de reinício do raciocínio quando o prefixo mudou (M4/TV-3.3)."""
    if record.get("prefix_changed"):
        return (
            "O contexto da conversa mudou desde o turno anterior "
            "(sistema, modelo, fontes, ferramentas ou anexos); o raciocínio "
            "recomeça a partir do contexto atual."
        )
    return None
