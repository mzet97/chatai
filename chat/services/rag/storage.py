"""Armazenamento privado de originais (RAG-02, RAG-10)."""

from pathlib import Path

from django.conf import settings


def rag_root() -> Path:
    base = getattr(settings, "RAG_STORAGE_DIR", None)
    root = Path(base) if base else Path(settings.BASE_DIR) / "data" / "rag"
    root.mkdir(parents=True, exist_ok=True)
    return root


def version_path(owner_id: int, base_uuid, doc_uuid, version_uuid, filename: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)[-100:]
    directory = rag_root() / str(owner_id) / str(base_uuid) / str(doc_uuid)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{version_uuid}_{safe}"


def write_original(dest: Path, content: bytes) -> None:
    dest.write_bytes(content)


def rag_object_store():
    """ObjectStore local enraizado no diretório RAG (M3).

    Mesmo layout byte a byte do caminho anterior (`version_path`): nenhuma
    migração de dados. O backend S3 do perfil homelab implementa a mesma
    ABC (ver docs/architecture-v2/migration.md).
    """
    from chat.services.storage import LocalObjectStore

    return LocalObjectStore(root=rag_root())


def delete_tree(path: Path) -> None:
    import shutil

    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
