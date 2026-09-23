"""M3: validação Pillow, limites e montagem de blocos (mocks: Pillow local)."""

import base64
import io

import pytest
from PIL import Image

from chat.services.images import (
    MAX_IMAGE_BYTES,
    MAX_IMAGE_PIXELS,
    MAX_IMAGES_PER_MESSAGE,
    MAX_SERIALIZED_BYTES,
    build_user_content,
    check_serialized_size,
    image_entries_of,
    validate_image_bytes,
    validate_message_images,
)


def _bytes(fmt="PNG", size=(64, 48), color=(10, 120, 200), **kw):
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kw)
    return buf.getvalue()


def test_formatos_aceitos_com_media_type():
    assert validate_image_bytes(_bytes("PNG"))["media_type"] == "image/png"
    assert validate_image_bytes(_bytes("JPEG"))["media_type"] == "image/jpeg"
    assert validate_image_bytes(_bytes("WEBP"))["media_type"] == "image/webp"
    gif = io.BytesIO()
    Image.new("RGB", (32, 32), (1, 2, 3)).save(gif, format="GIF")
    assert validate_image_bytes(gif.getvalue())["media_type"] == "image/gif"


def test_normalizada_tem_dimensoes_e_miniatura():
    item = validate_image_bytes(_bytes("PNG", size=(800, 600)))
    assert (item["width"], item["height"]) == (800, 600)
    assert item["thumbnail"].startswith("data:image/png;base64,")
    raw = base64.b64decode(item["thumbnail"].split(",", 1)[1])
    thumb = Image.open(io.BytesIO(raw))
    assert max(thumb.size) <= 320


def test_tipo_nao_imagem_rejeitado():
    with pytest.raises(ValueError):
        validate_image_bytes("não é imagem".encode())
    with pytest.raises(ValueError):
        validate_image_bytes(b"%PDF-1.4 falso")


def test_bmp_rejeitado():
    buf = io.BytesIO()
    Image.new("RGB", (16, 16)).save(buf, format="BMP")
    with pytest.raises(ValueError):
        validate_image_bytes(buf.getvalue())


def test_gif_animado_rejeitado():
    frames = [Image.new("RGB", (16, 16), (i * 40, 0, 0)) for i in range(3)]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], loop=0)
    with pytest.raises(ValueError, match="[Aa]nimada"):
        validate_image_bytes(buf.getvalue())


def test_vazio_e_oversize():
    with pytest.raises(ValueError, match="[Vv]azio"):
        validate_image_bytes(b"")
    with pytest.raises(ValueError, match="5 MiB"):
        validate_image_bytes(b"x" * (MAX_IMAGE_BYTES + 1))


def test_limite_20mp_fronteira():
    big = Image.new("L", (5000, 4000))  # exatamente 20 MP: passa
    buf = io.BytesIO()
    big.save(buf, format="PNG")
    assert validate_image_bytes(buf.getvalue())["width"] == 5000
    over = Image.new("L", (5000, 4001))  # 20 MP + 1 px: recusa
    buf = io.BytesIO()
    over.save(buf, format="PNG")
    with pytest.raises(ValueError, match="20 megapixels"):
        validate_image_bytes(buf.getvalue())
    assert MAX_IMAGE_PIXELS == 20_000_000


def test_limite_4_por_mensagem():
    item = {"media_type": "image/png", "data": base64.b64encode(_bytes()).decode()}
    assert len(validate_message_images([item] * MAX_IMAGES_PER_MESSAGE)) == 4
    with pytest.raises(ValueError, match="4 imagens"):
        validate_message_images([item] * (MAX_IMAGES_PER_MESSAGE + 1))


def test_itens_invalidos():
    item = {"media_type": "image/png", "data": base64.b64encode(_bytes()).decode()}
    with pytest.raises(ValueError):
        validate_message_images([{"media_type": "image/png", "data": "!!!"}])
    with pytest.raises(ValueError):
        validate_message_images([{"media_type": "image/bmp", "data": item["data"]}])
    with pytest.raises(ValueError):
        validate_message_images("nao-lista")
    with pytest.raises(ValueError):
        validate_message_images([{"media_type": "image/png"}])


def test_teto_serializado():
    check_serialized_size([{"data": "x" * 100}])
    with pytest.raises(ValueError, match="20 MiB"):
        check_serialized_size([{"data": "x" * (MAX_SERIALIZED_BYTES + 1)}])


def test_build_user_content():
    assert build_user_content("oi", []) == "oi"
    assert build_user_content("", []) == ""
    item = {"media_type": "image/png", "data": "QUJD"}
    blocks = build_user_content("olhe", [item])
    assert blocks[0] == {"type": "text", "text": "olhe"}
    assert blocks[1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"},
    }
    only = build_user_content("", [item])
    assert len(only) == 1 and only[0]["type"] == "image"


def test_image_entries_of_filtra():
    blocks = [
        {"type": "text", "text": "a"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QQ=="}},
        {"type": "image", "source": {"type": "base64", "media_type": "image/bmp", "data": "QQ=="}},
        "lixo",
    ]
    assert image_entries_of(blocks) == [{"media_type": "image/png", "data": "QQ=="}]
    assert image_entries_of(None) == []
