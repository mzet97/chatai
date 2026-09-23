"""Anexo visível de imagens no compositor (M3; segurança M5).

Fluxo stateless, separado do Documento RAG:
POST /api/images (multipart, campo `file`) valida + normaliza e devolve o
payload canônico (base64) + miniatura; o POST de envio referencia esses
payloads em JSON (`images: [{media_type, data}]`) e o servidor revalida
antes de persistir em `Message.blocks` e montar blocos `image` base64
na serialização para o provedor.

Limites: 4 imagens/mensagem, 5 MiB/arquivo, 20 MP/imagem,
20 MiB serializados/mensagem; quotas por conversa (M5): 40 imagens,
80 MiB armazenados. Formatos: JPEG, PNG, WebP e GIF estático
(animado é recusado).

Segurança (M5): nome de arquivo e pixels são dados NÃO confiáveis —
nunca autorização, nunca HTML/JS, nunca segredo em resposta. Nomes com
padrão de segredo são recusados (DLP); o resto é sanitizado. Sem OCR:
texto dentro da imagem vai ao modelo como pixels, não como instrução.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import os
import re

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGES_PER_MESSAGE = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_SERIALIZED_BYTES = 20 * 1024 * 1024
THUMBNAIL_MAX_SIDE = 320
# Quotas por conversa (M5): teto de retenção no SQLite local.
MAX_IMAGES_PER_CONVERSATION = 40
MAX_STORED_IMAGE_BYTES_PER_CONVERSATION = 80 * 1024 * 1024
# Nomes: limite de exibição; o resto é truncado (nunca eco cru).
IMAGE_NAME_MAX_LEN = 120
# Resultado binário de ferramenta (M4/TV-5.2): teto próprio por item,
# separado do teto de texto do executor (32 KiB nunca trunca Base64).
MAX_TOOL_IMAGES = 4
MAX_TOOL_IMAGE_BYTES = 5 * 1024 * 1024


class StoredImageRevoked(ValueError):
    """Variante persistida inválida (hash divergente ou carga ilegível).

    Tombstone não sensível (TV-5.4): nunca inclui bytes, base64 ou nomes —
    só o fato da revogação, invalidando payloads, previews e projeções
    futuras do anexo.
    """


MEDIA_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")

_FORMAT_TO_MEDIA = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


def _fail(message: str) -> ValueError:
    return ValueError(message)


# --- M5: DLP/prompt-injection em nomes (texto em pixel não tem OCR:
# vai ao modelo como imagem; aqui só o nome é inspecionável). ---

# Sinais de instrução embutida: NÃO recusam (nome continua sendo dado
# inerte e escapado na UI); existem para documentar/testar que o nome
# nunca vira autorização nem HTML.
INJECTION_CUES = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard previous",
    "system prompt",
    "reveal system",
    "jailbreak",
    "bypass",
    "diga a senha",
    "revele a chave",
    "ignore instruções anteriores",
)

# Padrões de segredo (DLP): nome que parece conter credencial é recusado
# para não transformar o anexo em cofre de segredo nem vazar no eco.
SECRET_PATTERNS = (
    re.compile(r"sk-ant-[\w-]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[bpas]-[\w-]+"),
    re.compile(r"ghp_[\w]{8,}"),
    re.compile(r"-----BEGIN .*PRIVATE KEY-----"),
    re.compile(r"api[_-]?key\s*[:=]", re.IGNORECASE),
    re.compile(r"password\s*[:=]", re.IGNORECASE),
)


def sanitize_filename(name: str | None) -> str:
    """Nome seguro p/ exibição: basename, sem controles, truncado.

    Nunca devolve caminho nem string vazia; nunca levanta.
    """
    base = os.path.basename((name or "").strip()) or "imagem"
    base = "".join(c for c in base if c.isprintable() and c not in '<>"&')
    base = re.sub(r"\s+", " ", base).strip() or "imagem"
    if len(base) > IMAGE_NAME_MAX_LEN:
        stem, dot, ext = base.rpartition(".")
        if dot and len(ext) <= 10:
            keep = IMAGE_NAME_MAX_LEN - len(ext) - 4
            base = stem[:keep] + "…" + dot + ext
        else:
            base = base[: IMAGE_NAME_MAX_LEN - 1] + "…"
    return base


def has_injection_cues(text: str | None) -> bool:
    """Diz se o texto livre contém sinais de instrução embutida."""
    lowered = (text or "").lower()
    return any(cue in lowered for cue in INJECTION_CUES)


def check_filename_safety(name: str | None) -> str:
    """Valida o nome do upload (M5): DLP + sanitização.

    Retorna o nome sanitizado. Levanta ValueError sanitizado (sem eco
    do segredo) se o nome parecer conter credencial.
    """
    raw = name or ""
    for pat in SECRET_PATTERNS:
        if pat.search(raw):
            raise _fail("Nome de arquivo parece conter segredo; renomeie antes de anexar.")
    return sanitize_filename(raw)


def conversation_image_usage(messages_blocks: list[list[dict]]) -> tuple[int, int]:
    """Conta (n_imagens, bytes_decodificados_est) nos blocos persistidos."""
    count, total = 0, 0
    for blocks in messages_blocks:
        for b in blocks or []:
            if not isinstance(b, dict) or b.get("type") != "image":
                continue
            data = (b.get("source") or {}).get("data") or ""
            count += 1
            total += len(data) * 3 // 4
    return count, total


def check_conversation_quota(
    messages_blocks: list[list[dict]], new_bytes: int = 0, new_count: int = 0
) -> None:
    """Teto de retenção por conversa (M5); levanta ValueError sanitizado."""
    count, total = conversation_image_usage(messages_blocks)
    if count + new_count > MAX_IMAGES_PER_CONVERSATION:
        raise _fail(f"Conversa excede {MAX_IMAGES_PER_CONVERSATION} imagens anexadas.")
    if total + new_bytes > MAX_STORED_IMAGE_BYTES_PER_CONVERSATION:
        raise _fail("Conversa excede 80 MiB de imagens anexadas.")


def validate_image_bytes(content: bytes) -> dict:
    """Valida e normaliza um arquivo de imagem.

    Retorna dict canônico: media_type, width, height, size_bytes,
    data (base64 da variante normalizada) e thumbnail (data URL PNG).
    Levanta ValueError sanitizado (sem detalhes internos).
    """
    if not content:
        raise _fail("Arquivo vazio.")
    if len(content) > MAX_IMAGE_BYTES:
        raise _fail(f"Imagem excede 5 MiB ({len(content)} bytes).")
    try:
        img = Image.open(io.BytesIO(content))
        img.load()
    except (UnidentifiedImageError, OSError):
        raise _fail("Tipo de imagem não suportado (JPEG, PNG, WebP, GIF).") from None
    if img.format not in _FORMAT_TO_MEDIA:
        raise _fail("Tipo de imagem não suportado (JPEG, PNG, WebP, GIF).")
    if getattr(img, "is_animated", False) or getattr(img, "n_frames", 1) > 1:
        raise _fail("Imagem animada não suportada; envie uma imagem estática.")
    width, height = img.size
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise _fail("Imagem excede 20 megapixels.")
    media_type = _FORMAT_TO_MEDIA[img.format]
    normalized = _normalize(img, content)
    thumb_url = _thumbnail_url(img)
    payload = base64.b64encode(normalized).decode("ascii")
    return {
        "media_type": media_type,
        "width": width,
        "height": height,
        "size_bytes": len(normalized),
        # Variante imutável (M4): hash da variante normalizada. Persistido
        # no bloco e reverificado a cada reencontro (restart, histórico,
        # retry); divergência revoga o anexo em vez de reenviá-lo.
        "sha256": hashlib.sha256(normalized).hexdigest(),
        "data": payload,
        "thumbnail": thumb_url,
    }


def _normalize(img: Image.Image, content: bytes) -> bytes:
    """Variante normalizada: orientação EXIF aplicada, metadados descartados."""
    fmt = img.format
    if fmt == "GIF":
        return content  # estático e validado: preserva bytes (transparência intacta)
    work = ImageOps.exif_transpose(img)
    out = io.BytesIO()
    if fmt == "JPEG":
        work = work.convert("RGB")
        out = io.BytesIO()
        work.save(out, format="JPEG", quality=92)
    elif fmt == "WEBP":
        work = work.convert("RGB")
        out = io.BytesIO()
        work.save(out, format="WEBP", quality=90)
    else:  # PNG: preserva modo (inclui transparência), sem metadados
        if work.mode == "P":
            work = work.convert("RGBA")
        work.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _thumbnail_url(img: Image.Image) -> str:
    """Miniatura PNG (lado maior <= 320px) como data URL para o compositor."""
    work = ImageOps.exif_transpose(img).copy()
    work.thumbnail((THUMBNAIL_MAX_SIDE, THUMBNAIL_MAX_SIDE))
    if work.mode not in ("RGB", "RGBA", "LA"):
        work = work.convert("RGB")
    out = io.BytesIO()
    work.save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def validate_message_images(items: list | None) -> list[dict]:
    """Valida a lista `images` do POST de envio; retorna canônicos.

    Cada item: {media_type, data (base64)}. Revalida tudo com Pillow
    (não confia no payload do cliente).
    """
    items = items or []
    if not isinstance(items, list):
        raise _fail("Campo images inválido.")
    if len(items) > MAX_IMAGES_PER_MESSAGE:
        raise _fail(f"Máximo de {MAX_IMAGES_PER_MESSAGE} imagens por mensagem.")
    canonical = []
    for item in items:
        if not isinstance(item, dict):
            raise _fail("Imagem inválida.")
        media_type = item.get("media_type")
        data = item.get("data")
        if media_type not in MEDIA_TYPES or not isinstance(data, str) or not data:
            raise _fail("Imagem inválida.")
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise _fail("Imagem inválida.") from None
        checked = validate_image_bytes(raw)
        canonical.append(checked)
    check_serialized_size(canonical)
    return canonical


def check_serialized_size(canonical: list[dict]) -> None:
    """Teto do custo serializado da mensagem (soma dos base64)."""
    total = sum(len(item.get("data") or "") for item in canonical)
    if total > MAX_SERIALIZED_BYTES:
        raise _fail("Imagens excedem 20 MiB serializados.")


def build_user_content(
    text: str, images: list[dict], *, extra_blocks: list[dict] | None = None
) -> str | list[dict]:
    """Conteúdo da mensagem atual para o provedor.

    Sem imagens e sem blocos extras: str (caminho inalterado).
    Com imagens: blocos Anthropic [{text}, {image, source base64}...] + extras.
    """
    blocks: list[dict] = []
    if text:
        blocks.append({"type": "text", "text": text})
    for item in images:
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": item["media_type"],
                    "data": item["data"],
                },
            }
        )
    if extra_blocks:
        blocks.extend(extra_blocks)
    if not blocks:
        return text
    if len(blocks) == 1 and blocks[0].get("type") == "text" and not images:
        return text
    return blocks


def image_entries_of(blocks: list | dict | None) -> list[dict]:
    """Extrai {media_type, data} dos blocos `image` persistidos (histórico/atual)."""
    out = []
    for b in blocks or []:
        if not isinstance(b, dict) or b.get("type") != "image":
            continue
        source = b.get("source") or {}
        if source.get("media_type") in MEDIA_TYPES and source.get("data"):
            out.append({"media_type": source["media_type"], "data": source["data"]})
    return out


def stored_image_block(item: dict) -> dict:
    """Bloco persistido em Message.blocks (carga + miniatura p/ histórico)."""
    block = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": item["media_type"],
            "data": item["data"],
        },
        "width": item["width"],
        "height": item["height"],
        "thumbnail": item["thumbnail"],
    }
    if item.get("sha256"):
        block["sha256"] = item["sha256"]
    return block


def verified_image_entries(blocks: list | dict | None) -> list[dict]:
    """Reencontro do anexo (M4): extrai + reverifica a variante imutável.

    Recomputa o sha256 da carga persistida e confere com o hash gravado
    no envio; blocos legados sem hash são aceitos (tolerância de leitura).
    Divergência ou carga ilegível → StoredImageRevoked (fail closed, sem
    eco de bytes). Usado em todo caminho que serializa para o provedor;
    listagem/histórico continuam na visão pública sem base64.
    """
    out = []
    for b in blocks or []:
        if not isinstance(b, dict) or b.get("type") != "image":
            continue
        source = b.get("source") or {}
        media_type, data = source.get("media_type"), source.get("data")
        if media_type not in MEDIA_TYPES or not isinstance(data, str) or not data:
            raise StoredImageRevoked("Anexo de imagem revogado (carga inválida).")
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise StoredImageRevoked("Anexo de imagem revogado (carga ilegível).") from None
        expected = b.get("sha256")
        if expected and hashlib.sha256(raw).hexdigest() != expected:
            raise StoredImageRevoked("Anexo de imagem revogado (variante alterada).")
        entry = {"media_type": media_type, "data": data}
        if expected:
            entry["sha256"] = expected
        out.append(entry)
    return out


def validate_tool_result_images(items: list | None) -> list[dict]:
    """Valida imagens vindas de resultado de ferramenta (M4/TV-5.2).

    Teto binário próprio (4 itens, 5 MiB cada, 20 MP); nunca passa pelo
    corte de 32 KiB do texto. Retorna canônicos (com sha256 + thumbnail).
    """
    items = items or []
    if not isinstance(items, list):
        raise _fail("Imagens do resultado inválidas.")
    if len(items) > MAX_TOOL_IMAGES:
        raise _fail(f"Máximo de {MAX_TOOL_IMAGES} imagens por resultado de ferramenta.")
    canonical = []
    for item in items:
        if not isinstance(item, dict):
            raise _fail("Imagem do resultado inválida.")
        media_type, data = item.get("media_type"), item.get("data")
        if media_type not in MEDIA_TYPES or not isinstance(data, str) or not data:
            raise _fail("Imagem do resultado inválida.")
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise _fail("Imagem do resultado inválida.") from None
        if len(raw) > MAX_TOOL_IMAGE_BYTES:
            raise _fail("Imagem do resultado excede 5 MiB.")
        canonical.append(validate_image_bytes(raw))
    return canonical


def tool_result_block(call_id: str, text: str, images: list[dict]) -> dict:
    """Bloco tool_result Anthropic com texto + imagens (M4/TV-5.2).

    Sem imagens: conteúdo string (caminho legado). Com imagens: lista de
    blocos [{text}, {image base64}...] — Base64 integral, sem truncamento.
    """
    if not images:
        return {"type": "tool_result", "tool_use_id": call_id, "content": text}
    content: list[dict] = [{"type": "text", "text": text or ""}]
    for item in images:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": item["media_type"],
                    "data": item["data"],
                },
            }
        )
    return {"type": "tool_result", "tool_use_id": call_id, "content": content}


def public_image(item_or_block: dict) -> dict:
    """Visão pública p/ listagem/histórico: metadados + miniatura, sem base64."""
    block = item_or_block
    if block.get("source"):
        src = block["source"]
        return {
            "media_type": src.get("media_type"),
            "width": block.get("width"),
            "height": block.get("height"),
            "thumbnail": block.get("thumbnail"),
        }
    return {
        "media_type": block.get("media_type"),
        "width": block.get("width"),
        "height": block.get("height"),
        "thumbnail": block.get("thumbnail"),
    }


def estimate_image_tokens(data_len: int) -> int:
    """Estimativa didática p/ orçamento sem contagem exata (~bytes/750)."""
    return max(50, (data_len * 3 // 4) // 750)
