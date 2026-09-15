"""M2: chunking por estrutura + orçamento do tokenizer (RAG-04).

Usa o tokenizer real do modelo preparado (sem rede); pula se ausente.
"""

import pytest

from chat.services.rag.chunk import (
    MAX_INPUT_TOKENS,
    OVERLAP_TOKENS,
    TARGET_TOKENS,
    chunk_extraction,
    prefixed,
)

try:
    from chat.services.rag.embed import get_tokenizer_encode

    _HAS_MODEL = True
except Exception:  # pragma: no cover
    _HAS_MODEL = False

needs_model = pytest.mark.skipif(not _HAS_MODEL, reason="modelo não preparado")


def _encode():
    try:
        return get_tokenizer_encode()
    except RuntimeError:
        pytest.skip("modelo não preparado (rode rag_prepare)")


@needs_model
def test_txt_respeita_paragrafos_e_orcamento():
    encode = _encode()
    paras = [f"Parágrafo {i} sobre prazos contratuais e multas." for i in range(40)]
    drafts = chunk_extraction("\n\n".join(paras), {"lines": list(range(1, 81))}, ".txt", encode)
    assert len(drafts) > 1
    for d in drafts:
        assert d.token_count <= MAX_INPUT_TOKENS
        assert d.token_count <= TARGET_TOKENS + 200  # alvo + 1 unidade
        assert d.context_hint == ""  # txt sem seção: nada sintético
        assert d.text and d.search_text.endswith(d.text)


@needs_model
def test_md_preserva_secoes_e_codigo():
    encode = _encode()
    text = (
        "# Contrato\n\nCláusula geral.\n\n## Prazo\n\nO prazo é 30 dias.\n\n"
        "```python\nprint('multa')\n```\n\n## Multa\n\nMulta de 2% ao mês."
    )
    locators = {"lines": list(range(1, 14)), "sections": [
        {"title": "Contrato", "level": 1, "line": 1},
        {"title": "Prazo", "level": 2, "line": 5},
        {"title": "Multa", "level": 2, "line": 11},
    ]}
    drafts = chunk_extraction(text, locators, ".md", encode)
    assert drafts
    prazo = [d for d in drafts if "30 dias" in d.text]
    assert prazo and "Prazo" in prazo[0].context_hint
    # Código não é citado como prosa, mas vai junto com sua seção.
    code = [d for d in drafts if "print('multa')" in d.text]
    assert code
    # Hint separado do texto citável.
    for d in drafts:
        assert d.context_hint not in d.text or not d.context_hint


@needs_model
def test_pdf_mantem_pagina_por_fragmento():
    encode = _encode()
    text = "[p1]\nO objeto do contrato.\n\n[p2]\nO prazo é 30 dias.\n\n[p3]\nA multa é 2%."
    drafts = chunk_extraction(
        text, {"pages": [1, 2, 3], "coverage": {1: True, 2: True, 3: True}}, ".pdf", encode
    )
    assert drafts
    pages = set()
    for d in drafts:
        spans = d.locator.get("spans", [d.locator])
        pages.update(s.get("page") for s in spans if "page" in s)
    assert pages == {1, 2, 3}


@needs_model
def test_docx_mapeia_blocos_e_linhas_de_tabela():
    encode = _encode()
    text = "Introdução do manual.\n\n[t1r1] nome | prazo\n\n[t1r2] contrato | 30 dias"
    drafts = chunk_extraction(text, {"blocks": ["s1/p1", "t1r1", "t1r2"]}, ".docx", encode)
    assert drafts
    assert any("30 dias" in d.text for d in drafts)
    blocks = set()
    for d in drafts:
        spans = d.locator.get("spans", [d.locator])
        blocks.update(s.get("block") for s in spans if "block" in s)
    assert {"s1/p1", "t1r1", "t1r2"} <= blocks


@needs_model
def test_sem_truncamento_silencioso_documento_longo():
    encode = _encode()
    paras = [f"Seção {i}: " + ("conteúdo relevante do parágrafo. " * 20) for i in range(30)]
    drafts = chunk_extraction("\n\n".join(paras), {"lines": [1]}, ".txt", encode)
    # Toda unidade aparece em algum chunk (assert interno), e o último
    # parágrafo não se perde mesmo com overlap.
    assert "Seção 29" in drafts[-1].text or any("Seção 29" in d.text for d in drafts)
    for d in drafts:
        assert len(encode(prefixed(d.search_text))) <= MAX_INPUT_TOKENS


@needs_model
def test_overlap_limitado_a_48_tokens():
    encode = _encode()
    paras = [f"Bloco {i}: " + ("palavra " * 60) for i in range(10)]
    drafts = chunk_extraction("\n\n".join(paras), {"lines": [1]}, ".txt", encode)
    assert len(drafts) >= 2
    for a, b in zip(drafts, drafts[1:], strict=False):
        shared = set(a.text.split()) & set(b.text.split())
        # Overlap existe (continuidade) mas limitado: mede em tokens.
        if shared:
            assert len(encode(" ".join(sorted(shared)))) <= OVERLAP_TOKENS + 20


@needs_model
def test_prefixo_conta_no_limite():
    encode = _encode()
    drafts = chunk_extraction("Texto curto.", {"lines": [1]}, ".txt", encode)
    assert len(drafts) == 1
    assert drafts[0].token_count == len(encode(prefixed(drafts[0].search_text)))
