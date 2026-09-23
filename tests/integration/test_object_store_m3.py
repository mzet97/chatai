"""M3: ObjectStore — binários imutáveis com SHA-256, staging e limpeza.

Sem rede, sem dependência nova. Sem commit.
Cobre: put/get/exists/delete; travessia de diretório bloqueada; staging
commit/abort atômicos; sweep de órfãos; layout RAG preservado byte a byte.
"""

import pytest

from chat.services.storage import LocalObjectStore, ObjectStore


def _store(tmp_path):
    return LocalObjectStore(root=tmp_path / "objs")


def test_put_get_roundtrip_with_hash(tmp_path):
    store = _store(tmp_path)
    meta = store.put("a/b.bin", b"conteudo")
    assert meta["size"] == 8
    assert len(meta["sha256"]) == 64
    assert store.get("a/b.bin") == b"conteudo"
    assert store.exists("a/b.bin") is True
    assert store.exists("nada.bin") is False
    assert store.delete("a/b.bin") is True
    assert store.exists("a/b.bin") is False
    assert store.delete("a/b.bin") is False
    with pytest.raises(FileNotFoundError):
        store.get("a/b.bin")


def test_path_traversal_blocked(tmp_path):
    store = _store(tmp_path)
    for evil in ("../fora.bin", "/abs.bin", "a/../../fora.bin", ""):
        with pytest.raises(ValueError):
            store.put(evil, b"x")
    leaked = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert leaked == []  # nada escapou da raiz (só dirs .staging vazios)


def test_staging_commit_abort(tmp_path):
    store = _store(tmp_path)
    token = store.stage(b"rascunho")
    assert store.exists("final.bin") is False
    meta = store.commit(token, "final.bin")
    assert store.get("final.bin") == b"rascunho"
    assert meta["size"] == 8
    with pytest.raises(ValueError):
        store.commit(token, "outra.bin")  # token de uso único
    token2 = store.stage(b"lixo")
    store.abort(token2)
    with pytest.raises(ValueError):
        store.commit(token2, "x.bin")  # abortado não commita
    with pytest.raises(ValueError):
        store.commit("token-invalido", "x.bin")


def test_sweep_removes_old_staged_only(tmp_path):
    import os
    import time

    store = _store(tmp_path)
    token = store.stage(b"velho")
    store.commit(store.stage(b"novo"), "novo.bin")
    staged_file = store._staged_path(token)
    old = time.time() - 7200
    os.utime(staged_file, (old, old))
    assert store.sweep_staged(max_age_s=3600) == 1  # só o staged velho
    assert store.exists("novo.bin") is True


def test_rag_layout_preserved(tmp_path):
    """O layout RAG continua byte-idêntico sob o ObjectStore (sem migração
    de dados: mesma raiz, mesma chave relativa)."""
    store = _store(tmp_path)
    key = "1/base/doc/ver_arquivo.txt"
    store.put(key, b"doc")
    assert (tmp_path / "objs" / key).read_bytes() == b"doc"
    assert isinstance(store, ObjectStore)
