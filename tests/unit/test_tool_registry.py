"""M1: registro unificado e validação de nomes/schemas (T2)."""

from chat.services.tools.registry import (
    ANTHROPIC_NAME_RE,
    ToolRecord,
    anthropic_name_for,
    build_payload,
)


def _rec(name, origin="local"):
    return ToolRecord(
        stable_id=f"{origin}:{name}",
        origin=origin,
        original_name=name,
        description=f"Ferramenta {name}.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        version="1",
        approval="auto",
    )


def test_nome_exibicao_nao_e_identificador():
    a = anthropic_name_for("mcp", "server-x", "read_lesson")
    b = anthropic_name_for("mcp", "server-y", "read_lesson")
    assert a != b  # colisão desambiguada por origem+hash
    assert ANTHROPIC_NAME_RE.match(a) and ANTHROPIC_NAME_RE.match(b)


def test_payload_sem_truncamento_colidente():
    recs = [_rec(f"tool_{i:03d}") for i in range(5)]
    names = [t["name"] for t in build_payload(recs)["tools"]]
    assert len(set(names)) == 5
    assert all(ANTHROPIC_NAME_RE.match(n) and len(n) <= 64 for n in names)


def test_origem_mcp_composta_no_nome():
    n = anthropic_name_for("mcp", "study-lessons", "list_lessons")
    assert "study" in n and "list_lessons" in n


def test_payload_preserva_schema_e_descricao():
    rec = _rec("calculate")
    (tool,) = build_payload([rec])["tools"]
    assert tool["input_schema"] == rec.input_schema
    assert tool["description"] == rec.description
    assert build_payload([rec])["tool_choice"] == {"type": "auto"}
