"""Modo de entrega: validação e resolução (streaming x complete)."""

import pytest

from chat.services.delivery import (
    COMPLETE,
    NO_REVIEW_GATE_REASON,
    STREAMING,
    normalize_mode,
    resolve_delivery,
)


def test_normalize_aceita_os_dois_modos():
    assert normalize_mode("streaming") == STREAMING
    assert normalize_mode("complete") == COMPLETE
    assert normalize_mode("  Streaming ") == STREAMING


def test_normalize_rejeita_fora_do_enum():
    with pytest.raises(ValueError):
        normalize_mode("turbo")
    with pytest.raises(ValueError):
        normalize_mode("")
    with pytest.raises(ValueError):
        normalize_mode(None)


def test_resolve_sem_gate_efetivo_igual_ao_solicitado():
    # Sem revisão prévia obrigatória no projeto, nada força o modo completo.
    assert resolve_delivery("streaming") == (STREAMING, None)
    assert resolve_delivery("complete") == (COMPLETE, None)
    assert resolve_delivery(None) == (STREAMING, None)


def test_gate_ausente_documentado():
    assert NO_REVIEW_GATE_REASON  # âncora textual da decisão (SDD §11)
