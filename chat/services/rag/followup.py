"""Consultas de acompanhamento (§8, M3).

Estratégia determinística/local: pergunta atual + contexto curto do último
tópico/pergunta do usuário na mesma conversa. No máximo uma consulta
auxiliar; sem reescrita por LLM (custo/chamada externa — evolução futura).
Original e efetiva sempre separadas no diagnóstico.
"""

from __future__ import annotations

from dataclasses import dataclass

SHORT_QUESTION_WORDS = 8
CONTEXT_WORDS = 20


@dataclass
class BuiltQuery:
    original: str
    effective: str
    aux_used: bool
    aux_source: str = ""  # "last_user_question" | ""


def _topic_of(question: str) -> str:
    words = (question or "").split()
    return " ".join(words[:CONTEXT_WORDS])


def build_query(question: str, last_user_question: str | None = None) -> BuiltQuery:
    """Monta a consulta pesquisada. Atual tem prioridade; sem redução oculta."""
    original = (question or "").strip()
    if not original:
        return BuiltQuery(original="", effective="", aux_used=False)
    short = len(original.split()) <= SHORT_QUESTION_WORDS
    topic = _topic_of(last_user_question) if last_user_question else ""
    if short and topic and topic.lower() not in original.lower():
        return BuiltQuery(
            original=original,
            effective=f"{original} {topic}",
            aux_used=True,
            aux_source="last_user_question",
        )
    return BuiltQuery(original=original, effective=original, aux_used=False)
