"""Validação de upload (RAG-02, RAG-10). Extensão+conteúdo; MIME não basta."""

import re

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 300
MAX_EXPANSION_RATIO = 4  # texto extraído <= 4x bytes originais

ALLOWED = {".txt", ".md", ".pdf", ".docx"}

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\- ]{0,200}[A-Za-z0-9._\-)]$|^[A-Za-z0-9]$")


def validate_upload(filename: str, content: bytes, size: int) -> str:
    """Retorna a extensão canônica ou levanta ValueError sanitizado."""
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise ValueError("Nome de arquivo inválido.")
    if not _SAFE_NAME.match(filename):
        raise ValueError("Nome de arquivo inválido.")
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED:
        raise ValueError(f"Extensão não permitida: {ext or '(ausente)'}.")
    if size < 0 or size != len(content):
        raise ValueError("Tamanho inconsistente.")
    if size == 0:
        raise ValueError("Arquivo vazio.")
    if size > MAX_FILE_BYTES:
        raise ValueError(f"Arquivo excede 20 MiB ({size} bytes).")
    _check_magic(ext, content)
    return ext


def _check_magic(ext: str, content: bytes) -> None:
    head = content[:8]
    if ext == ".pdf" and not head.startswith(b"%PDF-"):
        raise ValueError("Conteúdo não confere com a extensão .pdf.")
    if ext == ".docx" and not head.startswith(b"PK\x03\x04"):
        raise ValueError("Conteúdo não confere com a extensão .docx.")
    if ext in (".txt", ".md"):
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                content.decode("latin-1")
            except Exception:
                raise ValueError("Texto ilegível.") from None
    if content[:2] == b"MZ":
        raise ValueError("Executáveis não são permitidos.")
