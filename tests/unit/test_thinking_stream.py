"""M2: streaming de pensamento — eventos, persistência separada e replay.

Puros (sem banco/Django): espelham o estilo de test_thinking_caps.py.
"""

from types import SimpleNamespace

import pytest

from chat.services.thinking_stream import events as E
from chat.services.thinking_stream import replay as R
from chat.services.thinking_stream import store as S


def _raw(dtype, **kw):
    return SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type=dtype, **kw))


# --- eventos ---


def test_envelope_nao_muta_evento():
    ev = E.make_delta(text="oi")
    out = E.with_envelope(ev, run_id="r1", seq=7)
    assert out == {"run_id": "r1", "seq": 7, "type": "thinking_delta", "text": "oi"}
    assert ev == {"type": "thinking_delta", "text": "oi"}


def test_extrai_thinking_e_signature_e_ignora_texto():
    raw = [
        _raw("thinking_delta", thinking="penso "),
        _raw("text_delta", text="ola"),
        _raw("signature_delta", signature="sig1"),
        _raw("citations_delta"),
        SimpleNamespace(type="message_start"),
        _raw("redacted_thinking_delta"),
    ]
    assert list(E.iter_thinking_events(raw)) == [
        ("thinking", "penso "),
        ("signature", "sig1"),
        ("redacted", True),
    ]


def test_redacted_vira_evento_sem_conteudo():
    ev = E.make_redacted(reason="oculto")
    assert ev["type"] == "thinking_redacted" and "penso" not in str(ev)


# --- store: protocolo vs projeção ---


def test_protocolo_acumula_e_projecao_resume():
    log = S.new_log(display="summarized")
    S.append_delta(log, thinking="penso ", signature="s1")
    S.append_delta(log, thinking="logo existo")
    assert S.protocol_text(log) == "penso logo existo"
    assert S.has_signature(log) is True
    panel = S.project_panel(log)
    assert panel["state"] == "streaming"
    assert panel["summary"] == "penso logo existo"
    assert "s1" not in str(panel)  # signature nunca vaza p/ UI


def test_segmento_vazio_e_ignorado():
    log = S.new_log()
    S.append_delta(log)
    assert log["segments"] == []


def test_redacted_projecao_aviso_sem_conteudo():
    log = S.new_log()
    S.append_delta(log, thinking="secreto")
    S.mark_redacted(log, reason="modelo ocultou")
    panel = S.project_panel(log)
    assert panel["state"] == "redacted"
    assert panel["summary"] == "" and panel["reason"] == "modelo ocultou"


def test_log_vazio_projecao_ausente_ou_streaming():
    assert S.project_panel(None)["state"] == "absent"
    assert S.project_panel(S.complete(S.new_log()))["state"] == "absent"
    assert S.project_panel(S.new_log())["state"] == "streaming"


def test_resumo_trunca_com_marcador():
    log = {"display": "summarized", "redacted": False, "redact_reason": "",
           "completed": True,
           "segments": [{"thinking": "x" * 5000, "signature": ""}]}
    panel = S.project_panel(log)
    assert panel["summary"].endswith("…") and len(panel["summary"]) == 4001


def test_snapshot_roundtrip_sem_banco():
    log = S.complete(S.append_delta(S.new_log(display="summarized"), thinking="a", signature="s"))
    snap = S.to_snapshot({}, log)
    assert snap[S.SNAPSHOT_KEY]["version"] == S.PROTOCOL_VERSION
    back = S.from_snapshot(snap)
    assert S.protocol_text(back) == "a" and back["completed"] is True
    assert S.from_snapshot({}) is None


# --- replay com tool_result ---


def test_replay_monta_thinking_com_signature_mais_tool_result():
    log = S.append_delta(S.new_log(), thinking="penso", signature="sig")
    msgs = R.replay_messages(log, [{"tool_use_id": "t1", "content": "ok"}])
    assert msgs[0]["role"] == "assistant"
    block = msgs[0]["content"][0]
    assert block["type"] == "thinking" and block["signature"] == "sig"
    assert msgs[1] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
    }


def test_replay_sem_results_retorna_vazio():
    log = S.append_delta(S.new_log(), thinking="p", signature="s")
    assert R.replay_messages(log, []) == []


def test_replay_recusa_sem_signature_redacted_ou_sem_id():
    log_sem_sig = S.append_delta(S.new_log(), thinking="p")
    with pytest.raises(R.ReplayError):
        R.replay_messages(log_sem_sig, [{"tool_use_id": "t1", "content": "x"}])
    log_red = S.mark_redacted(S.append_delta(S.new_log(), thinking="p", signature="s"))
    with pytest.raises(R.ReplayError):
        R.replay_messages(log_red, [{"tool_use_id": "t1", "content": "x"}])
    with pytest.raises(R.ReplayError):
        R.replay_messages(None, [{"tool_use_id": "t1", "content": "x"}])
    with pytest.raises(R.ReplayError):
        R.build_continuation_blocks([{"content": "sem id"}])


def test_tool_result_is_error_preservado():
    blocks = R.build_continuation_blocks(
        [{"tool_use_id": "t9", "content": "falhou", "is_error": True}])
    assert blocks == [
        {"type": "tool_result", "tool_use_id": "t9", "content": "falhou", "is_error": True}]
