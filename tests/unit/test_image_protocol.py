"""M4 (unidade): variante imutável, reencontro verificado, protocolo,
RAG+imagens, tool_result com imagem e extração MCP."""

import base64
import io

import pytest
from PIL import Image

from chat.services import images as img
from chat.services import protocol as proto


def _bytes(fmt="PNG", size=(64, 48), color=(10, 120, 200)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


def _png_item(**kw):
    return img.validate_image_bytes(_bytes(**kw))


def test_variante_imutavel_mesmos_bytes_mesmo_hash():
    a = _png_item(color=(1, 2, 3))
    b = _png_item(color=(1, 2, 3))
    assert a["data"] == b["data"]
    assert a["sha256"] == b["sha256"]
    import hashlib

    assert a["sha256"] == hashlib.sha256(base64.b64decode(a["data"])).hexdigest()


def test_revalidacao_envio_preserva_variante():
    up = _png_item()
    again = img.validate_message_images([{"media_type": up["media_type"], "data": up["data"]}])[0]
    assert again["data"] == up["data"]
    assert again["sha256"] == up["sha256"]


def test_reencontro_verificado_round_trip():
    item = _png_item()
    entries = img.verified_image_entries([img.stored_image_block(item)])
    assert len(entries) == 1
    assert entries[0]["data"] == item["data"]
    assert entries[0]["sha256"] == item["sha256"]


def test_bloco_legado_sem_hash_eh_aceito():
    item = _png_item()
    block = img.stored_image_block(item)
    del block["sha256"]
    assert img.verified_image_entries([block])[0]["data"] == item["data"]


def _tampered_data(data: str) -> str:
    raw = bytearray(base64.b64decode(data))
    raw[100] ^= 0xFF
    return base64.b64encode(bytes(raw)).decode("ascii")


def test_variante_alterada_revoga_sem_eco_de_bytes():
    item = _png_item()
    block = img.stored_image_block(item)
    block["source"]["data"] = _tampered_data(item["data"])
    with pytest.raises(img.StoredImageRevoked) as exc:
        img.verified_image_entries([block])
    assert item["data"] not in str(exc.value)
    assert "data:image" not in str(exc.value)


def test_carga_ilegivel_revoga():
    block = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "!!!"},
        "sha256": "x",
    }
    with pytest.raises(img.StoredImageRevoked):
        img.verified_image_entries([block])


def test_tool_result_block_legado_e_com_imagem():
    legacy = img.tool_result_block("tu_1", "5", [])
    assert legacy == {"type": "tool_result", "tool_use_id": "tu_1", "content": "5"}
    item = _png_item()
    block = img.tool_result_block("tu_2", "veja", [item])
    assert block["tool_use_id"] == "tu_2"
    kinds = [b["type"] for b in block["content"]]
    assert kinds == ["text", "image"]
    assert block["content"][1]["source"]["data"] == item["data"]


def test_validate_tool_result_images_limites():
    item = _png_item()
    ok = img.validate_tool_result_images([{"media_type": item["media_type"], "data": item["data"]}])
    assert ok[0]["sha256"] == item["sha256"]
    with pytest.raises(ValueError):
        img.validate_tool_result_images(
            [{"media_type": item["media_type"], "data": item["data"]}] * 5
        )
    with pytest.raises(ValueError):
        img.validate_tool_result_images([{"media_type": "image/bmp", "data": item["data"]}])
    big = base64.b64encode(b"y" * (img.MAX_TOOL_IMAGE_BYTES + 1)).decode("ascii")
    with pytest.raises(ValueError, match="5 MiB"):
        img.validate_tool_result_images([{"media_type": "image/png", "data": big}])


def test_executor_gate_capacidade_imagem(monkeypatch):
    from chat.services.tools import executor as ex
    from chat.services.tools.registry import ToolRecord

    async def _handler(args, ctx):
        return {"ok": True, "text": "t", "images": [{"media_type": "x", "data": "y"}]}

    monkeypatch.setitem(ex.HANDLERS, "local__pic", _handler)
    base = dict(
        origin="local",
        original_name="pic",
        description="d",
        input_schema={"type": "object"},
        version="t1",
        approval="auto",
    )
    import asyncio

    from chat.services.tools.context import ExecutionContext

    ctx = ExecutionContext(user_id=1, conversation_id=2)
    rec_sem = ToolRecord(stable_id="local:pic", **base)
    out = asyncio.run(ex.execute("local__pic", {}, ctx, records=[rec_sem]))
    assert out["ok"] is False
    assert out["error"]["code"] == "images_not_supported"

    rec_com = ToolRecord(stable_id="local:pic", supports_images=True, **base)
    out = asyncio.run(ex.execute("local__pic", {}, ctx, records=[rec_com]))
    assert out["ok"] is False
    assert out["error"]["code"] == "invalid_images"


def test_protocolo_mesmo_prefixo_mesma_geracao():
    current = proto.build_record(
        system="s",
        model="m",
        rag_status="skipped",
        tools_enabled=[],
        image_entries=[],
    )
    tracked = proto.track(None, current)
    assert (tracked["generation"], tracked["prefix_changed"]) == (1, False)
    again = proto.track(tracked, current)
    assert (again["generation"], again["prefix_changed"]) == (1, False)
    assert proto.restart_warning(again) is None


def test_protocolo_mudanca_gera_nova_versao_e_aviso():
    prev = proto.track(
        None,
        proto.build_record(
            system="s", model="m", rag_status="skipped", tools_enabled=[], image_entries=[]
        ),
    )
    for kwargs in (
        {"system": "s2"},
        {"model": "m2"},
        {"rag_status": "ready", "rag_run_id": 7},
        {"tools_enabled": ["local:calculate"]},
    ):
        base = {
            "system": "s",
            "model": "m",
            "rag_status": "skipped",
            "tools_enabled": [],
            "image_entries": [],
        }
        base.update(kwargs)
        nxt = proto.track(prev, proto.build_record(**base))
        assert nxt["prefix_changed"] is True
        assert nxt["generation"] == 2
        assert "mudou" in (proto.restart_warning(nxt) or "")
    # Snapshot nunca carrega base64: só hashes e contagens.
    blob = str(prev)
    assert "base64" not in blob


def test_rag_search_blocks_sem_duplicar_texto():
    from types import SimpleNamespace

    from chat.services.rag import answer as ans

    hit = SimpleNamespace(kb_id="kb://x", doc_name="doc", version_number=1, text="trecho")
    assert ans.build_search_blocks([hit]) == [
        {
            "type": "search_result",
            "source": "kb://x",
            "title": "doc (v1)",
            "content": [{"type": "text", "text": "trecho"}],
        }
    ]
    full = ans.build_user_content([hit], "pergunta?")
    assert [b["type"] for b in full] == ["search_result", "text"]

    item = _png_item()
    composed = img.build_user_content(
        "pergunta?", [item], extra_blocks=ans.build_search_blocks([hit])
    )
    assert [b["type"] for b in composed] == ["text", "image", "search_result"]
    assert sum(1 for b in composed if b["type"] == "text") == 1


def test_mcp_split_result_blocks():
    from chat.services.tools import mcp_client

    content = [
        {"type": "text", "text": "oi"},
        {"type": "image", "data": "QUJD", "mimeType": "image/png"},
        {"type": "audio", "data": "xxx"},
    ]
    texts, images, unsupported = mcp_client.split_result_blocks(content)
    assert texts == ["oi"]
    assert images == [{"media_type": "image/png", "data": "QUJD"}]
    assert unsupported is True  # áudio segue fora de escopo

    texts, images, unsupported = mcp_client.split_result_blocks(
        [{"type": "text", "text": "só texto"}]
    )
    assert (images, unsupported) == ([], False)
