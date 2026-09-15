"""Extração com proveniência (RAG-02). Texto canônico + localizadores."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from chat.services.rag.validate import MAX_EXPANSION_RATIO, MAX_PDF_PAGES


@dataclass
class Extraction:
    text: str
    locators: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    needs_ocr: bool = False
    extractor: str = ""


def extract_document(source: Path | bytes, ext: str) -> Extraction:
    data = Path(source).read_bytes() if isinstance(source, Path) else bytes(source)
    if ext in (".txt", ".md"):
        return _extract_text(data, ext)
    if ext == ".pdf":
        return _extract_pdf(data)
    if ext == ".docx":
        return _extract_docx(data)
    raise ValueError(f"Extensão não suportada: {ext}.")


def _check_expansion(text: str, size: int, extractor: str) -> list:
    if size > 0 and len(text) > MAX_EXPANSION_RATIO * size:
        return [f"{extractor}: expansão anormal, verificado parcialmente"]
    return []


def _extract_text(data: bytes, ext: str) -> Extraction:
    try:
        raw = data.decode("utf-8")
    except UnicodeDecodeError:
        raw = data.decode("latin-1")
    lines = raw.splitlines()
    text = "\n".join(lines)
    locators: dict = {"lines": list(range(1, len(lines) + 1))}
    if ext == ".md":
        sections = []
        for i, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                level = len(stripped) - len(stripped.lstrip("#"))
                sections.append(
                    {"title": stripped.strip("# ").strip(), "level": level, "line": i}
                )
        locators["sections"] = sections
    return Extraction(
        text=text,
        locators=locators,
        warnings=_check_expansion(text, len(data), "text"),
        extractor=f"text:{ext}",
    )


def _extract_pdf(data: bytes) -> Extraction:
    from pypdf import PdfReader

    try:
        reader = PdfReader(__import__("io").BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("PDF protegido não suportado.")
        npages = len(reader.pages)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"PDF ilegível ou corrompido: {type(exc).__name__}.") from exc
    if npages > MAX_PDF_PAGES:
        raise ValueError(f"PDF excede {MAX_PDF_PAGES} páginas.")
    if npages == 0:
        return Extraction(text="", locators={"pages": [], "coverage": {}},
                          warnings=["PDF sem páginas."], needs_ocr=True,
                          extractor="pypdf")
    parts, coverage, warnings = [], {}, []
    for i, page in enumerate(reader.pages, start=1):
        try:
            t = page.extract_text() or ""
        except Exception:
            t, warnings = "", warnings + [f"página {i}: falha de extração"]
            coverage[i] = False
            continue
        t = t.strip()
        coverage[i] = bool(t)
        if t:
            parts.append(f"[p{i}]\n{t}")
    text = "\n\n".join(parts)
    needs_ocr = not any(coverage.values())
    if not needs_ocr and not all(coverage.values()):
        missing = sorted(p for p, ok in coverage.items() if not ok)
        warnings.append(f"Cobertura parcial: sem texto nas páginas {missing}.")
    warnings += _check_expansion(text, len(data), "pypdf")
    return Extraction(
        text=text,
        locators={"pages": list(range(1, npages + 1)), "coverage": coverage},
        warnings=warnings,
        needs_ocr=needs_ocr,
        extractor="pypdf",
    )


def _extract_docx(data: bytes) -> Extraction:
    import io
    import zipfile

    if len(data) > 20 * 1024 * 1024:
        raise ValueError("Arquivo excede 20 MiB.")
    # ZIP bomb: razão compressão/tamanho antes do parse.
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        total = sum(i.file_size for i in zf.infolist())
        if total > 200 * 1024 * 1024:
            raise ValueError("DOCX com expansão excessiva (ZIP bomb).")
        for info in zf.infolist():
            if info.filename.startswith("/") or ".." in info.filename:
                raise ValueError("DOCX com caminho inseguro.")
    def _text(el) -> str:
        return "".join(
            (node.text or "") for node in el.iter() if node.tag.endswith("}t")
        ).strip()

    try:
        from docx import Document as DX

        doc = DX(io.BytesIO(data))
    except Exception as exc:
        raise ValueError(f"DOCX ilegível ou corrompido: {type(exc).__name__}.") from exc
    parts, blocks = [], []
    par, tbl = 0, 0
    for el in doc.element.body:
        tag = el.tag.split("}")[-1]
        if tag == "p":
            t = _text(el)
            if not t:
                continue
            par += 1
            parts.append(t)
            blocks.append(f"s1/p{par}")
        elif tag == "tbl":
            tbl += 1
            for ri, tr in enumerate(el.findall(".//{*}tr"), start=1):
                cells = [
                    _text(tc) for tc in tr.findall("{*}tc")
                ]
                if any(cells):
                    parts.append(f"[t{tbl}r{ri}] " + " | ".join(cells))
                    blocks.append(f"t{tbl}r{ri}")
    text = "\n\n".join(parts)
    return Extraction(
        text=text,
        locators={"blocks": blocks},
        warnings=_check_expansion(text, len(data), "python-docx"),
        extractor="python-docx",
    )
