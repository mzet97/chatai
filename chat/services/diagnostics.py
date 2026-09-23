"""Diagnóstico em 4 passos distinguíveis (RF-06)."""

from __future__ import annotations

from chat.services import configuration as cfg
from chat.services.anthropic_client import classify_error
from chat.services.providers import get_provider


async def diagnose(
    *, secret: str | None, base_url: str, timeout: int, retries: int, client=None
) -> list[dict]:
    """Passos: 1 config, 2 cliente, 3 autenticação (list), 4 geração (NÃO executado aqui).

    O passo 4 exige autorização explícita (consome tokens) e roda em `diagnose_generation`.
    """
    steps = []
    if not secret:
        steps.append({"id": "config", "ok": False, "detail": "Nenhuma chave configurada."})
        return steps
    try:
        cfg.validate_base_url(base_url)
    except ValueError as exc:
        steps.append({"id": "config", "ok": False, "detail": f"Endpoint inválido: {exc}"})
        return steps
    steps.append(
        {
            "id": "config",
            "ok": True,
            "detail": f"Configuração carregada (endpoint {base_url}). Não é autenticação.",
        }
    )
    try:
        real = (
            client
            if client is not None
            else get_provider().build_client(
                api_key=secret, base_url=base_url, timeout_seconds=timeout, max_retries=retries
            )
        )
        steps.append(
            {"id": "client", "ok": True, "detail": "Cliente instanciado. Não é autenticação."}
        )
    except Exception as exc:
        steps.append({"id": "client", "ok": False, "detail": f"Falha ao instanciar: {exc}"})
        return steps
    try:
        page = await real.models.list()
        n = len(page.data)
        steps.append(
            {
                "id": "auth",
                "ok": True,
                "detail": f"Autenticação aceita: listagem retornou {n} modelo(s). "
                "Listar não prova que gerações funcionarão.",
            }
        )
    except Exception as exc:
        code, msg = classify_error(exc)
        steps.append({"id": "auth", "ok": False, "code": code, "detail": msg})
    return steps


async def diagnose_generation(*, client, model: str) -> dict:
    """Teste de geração pequeno, explicitamente autorizado. Retorna dict de resultado."""
    try:
        msg = await client.messages.create(
            model=model, max_tokens=16, messages=[{"role": "user", "content": "Diga apenas: ok"}]
        )
        text = "".join(b.text for b in (msg.content or []) if getattr(b, "type", "") == "text")
        usage = getattr(msg, "usage", None)
        return {
            "ok": True,
            "model": getattr(msg, "model", model),
            "text": text[:200],
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }
    except Exception as exc:
        code, detail = classify_error(exc)
        return {"ok": False, "code": code, "detail": detail}
