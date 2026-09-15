"""Registro unificado de ferramentas (T2).

Identidade estável ≠ nome de exibição. Nomes Anthropic obedecem ao contrato
`^[A-Za-z0-9_-]{1,64}$`; colisões se resolvem com origem + hash, sem truncar
para algo ambíguo.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

ANTHROPIC_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_LEN = 64


@dataclass(frozen=True)
class ToolRecord:
    stable_id: str  # "local:calculate" | "mcp:<conn-uuid>:<original>"
    origin: str  # "local" | "mcp"
    original_name: str
    description: str
    input_schema: dict
    version: str
    approval: str  # "auto" | "require" | "deny"
    scope: str = ""  # alias da conexão MCP; vazio no local
    extra: dict = field(default_factory=dict)

    @property
    def anthropic_name(self) -> str:
        return anthropic_name_for(self.origin, self.scope, self.original_name)


def _sanitize(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", part)


def anthropic_name_for(origin: str, scope: str, original: str) -> str:
    """Nome enviado ao modelo. Inclui origem/escopo: servidores distintos com a
    mesma ferramenta nunca colidem; hash curto só se exceder 64 chars."""
    if origin == "local":
        base = f"local__{original}"
    else:
        base = f"mcp__{scope}__{original}"
    base = _sanitize(base)
    if len(base) <= _MAX_LEN:
        return base
    digest = hashlib.sha256(base.encode()).hexdigest()[:8]
    return f"{base[: _MAX_LEN - 9]}_{digest}"


def find_by_anthropic_name(records: list[ToolRecord], name: str) -> ToolRecord | None:
    for rec in records:
        if rec.anthropic_name == name:
            return rec
    return None


def build_payload(records: list[ToolRecord]) -> dict:
    """`tools` + `tool_choice` para `messages.create`. Vazio = sem chave `tools`."""
    from chat.services.tools.limits import MAX_TOOLS_EXPOSED

    if not records:
        return {}
    if len(records) > MAX_TOOLS_EXPOSED:
        raise ValueError(f"Ferramentas demais: {len(records)} (máx. {MAX_TOOLS_EXPOSED}).")
    tools: list[dict[str, Any]] = [
        {
            "name": rec.anthropic_name,
            "description": rec.description,
            "input_schema": rec.input_schema,
        }
        for rec in records
    ]
    names = [t["name"] for t in tools]
    if len(set(names)) != len(names):
        raise ValueError(f"Nomes Anthropic colidiram: {names}.")
    for name in names:
        if not ANTHROPIC_NAME_RE.match(name):
            raise ValueError(f"Nome fora do contrato Anthropic: {name!r}.")
    return {"tools": tools, "tool_choice": {"type": "auto"}}
