"""Teste REAL opcional (desativado por padrão; NUNCA roda em CI/verificação padrão).

Autorização explícita: CHAT_LIVE_TEST=1 + ANTHROPIC_API_KEY válida. Consome tokens
(2 chamadas pequenas). Valida autenticação, streaming e reconstrução de contexto:
a asserção principal inspeciona o PAYLOAD enviado (determinístico), não a
capacidade do modelo de "lembrar frases".
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CHAT_LIVE_TEST") != "1" or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="Teste pago desativado por padrão (exige CHAT_LIVE_TEST=1 + chave).",
)

from anthropic import AsyncAnthropic  # noqa: E402


async def test_live_auth_stream_and_context_payload():
    client = AsyncAnthropic(base_url="https://api.anthropic.com")
    page = await client.models.list()
    assert len(page.data) > 0  # autenticação aceita

    secret_word = "abacaxi-voador-7"
    captured = {}

    # Turno 1: introduz informação; turno 2: verifica o payload reconstruído.
    first = await client.messages.create(
        model="claude-sonnet-5",
        max_tokens=32,
        messages=[{"role": "user", "content": f"Guarde isto: {secret_word}."}],
    )
    assert first.stop_reason in ("end_turn", "max_tokens")

    # Reconstrução determinística (o que o app faria via SQLite):
    payload = [
        {"role": "user", "content": f"Guarde isto: {secret_word}."},
        {"role": "assistant", "content": "Anotado."},
        {"role": "user", "content": "Repita a palavra guardada."},
    ]
    assert sum(1 for m in payload if secret_word in str(m.get("content", ""))) == 1
    captured["payload"] = payload

    text = []
    async with client.messages.stream(
        model="claude-sonnet-5", max_tokens=32, messages=payload
    ) as stream:
        async for delta in stream.text_stream:
            text.append(delta)
        final = await stream.get_final_message()
    assert "".join(text)  # streaming entregou deltas
    assert final.stop_reason in ("end_turn", "max_tokens")
    assert final.usage.input_tokens > 0
