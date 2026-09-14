"""Critério 8 (RF-04) + allowlist de endpoint (critério 16)."""

import pytest

from chat.services import configuration as cfg


def test_precedence_conversation_ui_env_file_default(monkeypatch):
    monkeypatch.setenv("CHAT_X", "from-env")
    cfg._file_env["CHAT_X"] = "from-file"
    assert cfg.resolve_option("CHAT_X", "conv", "ui") == ("conv", "conversation")
    assert cfg.resolve_option("CHAT_X", None, "ui") == ("ui", "ui")
    assert cfg.resolve_option("CHAT_X") == ("from-env", "env")
    monkeypatch.delenv("CHAT_X")
    assert cfg.resolve_option("CHAT_X") == ("from-file", "file")
    del cfg._file_env["CHAT_X"]
    cfg.DEFAULTS["CHAT_X"] = "dflt"
    assert cfg.resolve_option("CHAT_X") == ("dflt", "default")
    del cfg.DEFAULTS["CHAT_X"]


def test_credential_never_falls_back_to_plaintext(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg._file_env["ANTHROPIC_API_KEY"] = ""
    cred = cfg.resolve_credential(use_keychain=False)
    assert cred.secret is None and cred.origin == "none"


def test_credential_env_beats_file(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    cfg._file_env["ANTHROPIC_API_KEY"] = "sk-file"
    cred = cfg.resolve_credential(use_keychain=False)
    assert (cred.secret, cred.origin) == ("sk-env", "env")


def test_endpoint_allowlist():
    assert cfg.validate_base_url("https://api.anthropic.com") is not None
    with pytest.raises(ValueError):
        cfg.validate_base_url("http://api.anthropic.com")  # sem http puro
    with pytest.raises(ValueError):
        cfg.validate_base_url("https://evil.example.com")  # fora da allowlist
    with pytest.raises(ValueError):
        cfg.validate_base_url("https://user:pass@api.anthropic.com")  # sem credencial embutida
