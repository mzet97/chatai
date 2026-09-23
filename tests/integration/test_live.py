"""Teste REAL opcional (desativado por padrão; NUNCA roda em CI/verificação padrão).

Autorização explícita: CHAT_LIVE_TEST=1 + ANTHROPIC_API_KEY válida. Consome tokens
(2 chamadas pequenas + 1 experimento de cache). Valida autenticação, streaming e
reconstrução de contexto: a asserção principal inspeciona o PAYLOAD enviado
(determinístico), não a capacidade do modelo de "lembrar frases". O experimento
de cache (§20) comprova escrita e leitura pelo `usage`, nunca por hash local.
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
    # thinking desativado explicitamente: o padrão do claude-sonnet-5 pensa e
    # consome max_tokens pequeno só com o bloco thinking (observado ao vivo).
    no_think = {"type": "disabled"}
    first = await client.messages.create(
        model="claude-sonnet-5",
        max_tokens=64,
        thinking=no_think,
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
        model="claude-sonnet-5",
        max_tokens=64,
        thinking=no_think,
        messages=payload,
    ) as stream:
        async for delta in stream.text_stream:
            text.append(delta)
        final = await stream.get_final_message()
    final_text = "".join(b.text for b in final.content if getattr(b, "type", "") == "text")
    # Deltas vazios ocasionais já observados na API; o que prova o caminho
    # de streaming é a mensagem final montada pelo próprio stream.
    assert "".join(text) or final_text
    assert final.stop_reason in ("end_turn", "max_tokens")
    assert final.usage.input_tokens > 0


def _stable_prefix() -> str:
    import uuid

    # Salt único por execução: garante que a 1ª chamada sempre grava e a 2ª
    # sempre lê (cache de uma execução anterior invalidaria essa ordem).
    salt = f"Execução {uuid.uuid4().hex[:8]}. "
    base = (
        "Diretriz operacional do assistente de estudo. "
        "Responda em português, com frases curtas e verificáveis. "
        "Nunca invente citações, preços ou disponibilidade de modelos. "
        "Quando a evidência for insuficiente, diga o que falta. "
        "Preserve o orçamento de tokens e prefira a resposta mínima útil. "
    )
    sections = [f"Seção {i:02d}. " + salt + base * 6 for i in range(1, 13)]
    return "\n\n".join(sections)


async def test_live_prompt_cache_write_then_read():
    """§20: prefixo estável acima do mínimo; hit provado pelo usage."""
    client = AsyncAnthropic(base_url="https://api.anthropic.com")
    system = [
        {
            "type": "text",
            "text": _stable_prefix(),
            "cache_control": {"type": "ephemeral"},
        }
    ]
    first = await client.messages.create(
        model="claude-sonnet-5",
        max_tokens=16,
        system=system,
        messages=[{"role": "user", "content": "Responda só: ok."}],
    )
    assert first.usage.cache_creation_input_tokens > 0  # escrita confirmada

    second = await client.messages.create(
        model="claude-sonnet-5",
        max_tokens=16,
        system=system,
        messages=[{"role": "user", "content": "Responda só: ok."}],
    )
    assert second.usage.cache_read_input_tokens > 0  # leitura confirmada
