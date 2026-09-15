"""M1: extração dos 4 formatos com proveniência (RAG-02)."""

from pathlib import Path

import pytest

from chat.services.rag.extract import extract_document
from chat.services.rag.validate import validate_upload

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "rag"


def test_txt_preserva_linhas():
    out = extract_document(FIX / "sample.txt", ".txt")
    assert "30 dias corridos" in out.text
    assert out.locators["lines"] == [1, 2, 3, 4]
    assert out.warnings == []


def test_md_preserva_titulos_e_codigo():
    out = extract_document(FIX / "sample.md", ".md")
    assert "REEMBOLSO-7" in out.text
    sections = [loc for loc in out.locators["sections"]]
    assert sections[0]["title"] == "Política de Reembolso"
    assert any(s["title"] == "Prazos" for s in sections)


def test_pdf_por_pagina_com_numeracao():
    out = extract_document(FIX / "sample.pdf", ".pdf")
    assert "5 dias úteis" in out.text
    assert out.locators["pages"] == [1, 2]
    assert "p1" in out.text or out.locators["coverage"] == {1: True, 2: True}


def test_pdf_sem_texto_marca_needs_ocr():
    out = extract_document(FIX / "empty.pdf", ".pdf")
    assert out.text.strip() == ""
    assert out.needs_ocr is True


def test_pdf_corrompido_erro_sanitizado():
    with pytest.raises(ValueError, match="ilegível|corrompido|extração"):
        extract_document(FIX / "corrupt.pdf", ".pdf")


def test_docx_ordem_paragrafos_tabelas():
    out = extract_document(FIX / "sample.docx", ".docx")
    idx_garantia = out.text.index("12 meses adicionais")
    idx_tabela = out.text.index("Básico")
    idx_fim = out.text.index("planos disponíveis")
    assert idx_garantia < idx_tabela < idx_fim
    assert any("t1" in str(loc) for loc in out.locators["blocks"])


def test_validate_rejeita_executavel_e_traversal():
    with pytest.raises(ValueError, match="(?i)extensão|formato"):
        validate_upload("virus.exe", b"MZ" + b"\x00" * 100, 102)
    with pytest.raises(ValueError, match="(?i)nome"):
        validate_upload("../../etc/passwd", b"oi", 2)
    with pytest.raises(ValueError, match="tamanho|MiB|excede"):
        big = b"x" * (21 * 1024 * 1024)
        validate_upload("big.txt", big, len(big))


def test_validate_confere_magic_nao_mime():
    # .pdf com conteúdo texto puro não é PDF válido
    with pytest.raises(ValueError, match="(?i)conteúdo|assinatura|magic"):
        validate_upload("falso.pdf", b"apenas texto, sem cabecalho pdf", 31)
