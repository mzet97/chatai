"""Único ponto de construção do cliente Anthropic. Nada de SDK em views/models.

`build_client` cria o `AsyncAnthropic` real com parâmetros explícitos.
Para testes, passe um cliente falso em `client=` nas funções de serviço
(injeção de dependência — sem monkeypatch de APIs internas do SDK).
"""

from __future__ import annotations

from typing import Any

import anthropic

from chat.services import configuration as cfg


def build_client(*, api_key: str, base_url: str, timeout_seconds: int, max_retries: int) -> Any:
    """Cria AsyncAnthropic com configuração explícita (passo 2 do diagnóstico)."""
    return anthropic.AsyncAnthropic(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout_seconds,
        max_retries=max_retries,
    )


class ResolvedConfig:
    """Configuração efetiva (sem segredo serializável)."""

    def __init__(
        self,
        *,
        model: str,
        model_origin: str,
        base_url: str,
        base_url_origin: str,
        timeout_seconds: int,
        timeout_origin: str,
        max_retries: int,
        retries_origin: str,
        max_output_tokens: int,
        output_origin: str,
        input_budget: int,
        budget_origin: str,
        key_origin: str,
    ):
        self.model = model
        self.model_origin = model_origin
        self.base_url = base_url
        self.base_url_origin = base_url_origin
        self.timeout_seconds = timeout_seconds
        self.timeout_origin = timeout_origin
        self.max_retries = max_retries
        self.retries_origin = retries_origin
        self.max_output_tokens = max_output_tokens
        self.output_origin = output_origin
        self.input_budget = input_budget
        self.budget_origin = budget_origin
        self.key_origin = key_origin

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "model_origin": self.model_origin,
            "base_url": self.base_url,
            "base_url_origin": self.base_url_origin,
            "timeout_seconds": self.timeout_seconds,
            "timeout_origin": self.timeout_origin,
            "max_retries": self.max_retries,
            "retries_origin": self.retries_origin,
            "max_output_tokens": self.max_output_tokens,
            "output_origin": self.output_origin,
            "input_budget": self.input_budget,
            "budget_origin": self.budget_origin,
            "key_origin": self.key_origin,  # origem da credencial, NUNCA o segredo
        }


def resolve_for_user(user, conversation=None) -> tuple[ResolvedConfig, cfg.Credential]:
    """Resolve configuração efetiva para (usuário, conversa opcional)."""
    from chat.models import ConnectionSettings

    ui = ConnectionSettings.objects.filter(owner=user).first()
    conv_model = getattr(conversation, "preferred_model", None) or None
    conv_max_out = getattr(conversation, "max_output_tokens", None)
    conv_budget = getattr(conversation, "input_budget", None)
    ui_model = (ui.default_model if ui else "") or None
    ui_base = (ui.base_url if ui else "") or None
    ui_timeout = ui.timeout_seconds if ui else None
    ui_retries = ui.max_retries if ui else None

    model, model_origin = cfg.resolve_option("ANTHROPIC_MODEL", conv_model, ui_model)
    base_url, base_url_origin = cfg.resolve_option("ANTHROPIC_BASE_URL", None, ui_base)
    timeout, timeout_origin = cfg.resolve_int("CHAT_API_TIMEOUT_SECONDS", None, ui_timeout)
    retries, retries_origin = cfg.resolve_int("CHAT_API_MAX_RETRIES", None, ui_retries)
    max_out, out_origin = cfg.resolve_int("CHAT_MAX_OUTPUT_TOKENS", conv_max_out, None)
    budget, budget_origin = cfg.resolve_int("CHAT_INPUT_TOKEN_BUDGET", conv_budget, None)
    cred = cfg.resolve_credential(use_keychain=bool(ui and ui.keychain_ref))
    return ResolvedConfig(
        model=model,
        model_origin=model_origin,
        base_url=base_url,
        base_url_origin=base_url_origin,
        timeout_seconds=timeout,
        timeout_origin=timeout_origin,
        max_retries=retries,
        retries_origin=retries_origin,
        max_output_tokens=max_out,
        output_origin=out_origin,
        input_budget=budget,
        budget_origin=budget_origin,
        key_origin=cred.origin,
    ), cred


# --- erros: SDK → códigos estáveis ---


def classify_error(exc: BaseException) -> tuple[str, str]:
    """(código estável, mensagem útil sem vazar segredo)."""
    from anthropic import (
        APIConnectionError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        NotFoundError,
        PermissionDeniedError,
        RateLimitError,
    )

    if isinstance(exc, AuthenticationError):
        return "unauthorized", "Chave rejeitada (401). Verifique a chave e o endpoint."
    if isinstance(exc, PermissionDeniedError):
        return "forbidden", "Acesso negado (403) para esta operação."
    if isinstance(exc, NotFoundError):
        return "model_unavailable", "Modelo ou recurso não encontrado (404)."
    if isinstance(exc, RateLimitError):
        return "rate_limited", "Limite de taxa atingido (429). Aguarde e tente de novo."
    if isinstance(exc, APITimeoutError):
        return "api_timeout", "Tempo esgotado aguardando a API."
    if isinstance(exc, APIConnectionError):
        return "api_connection", "Falha de conexão com a API."
    if isinstance(exc, BadRequestError):
        msg = str(exc)
        if "max_tokens" in msg and ("exceed" in msg or "too high" in msg):
            return "context_too_large", "Limite de saída/contexto excedido."
        if "temperature" in msg.lower():
            # Rejeição específica do parâmetro (ex.: modelo que não aceita):
            # falha de compatibilidade, nunca "chave inválida" ou "saldo".
            code = "temperature_unsupported"
            return code, "Este ajuste não está disponível no modelo selecionado."
        return "api_error", "Requisição rejeitada pela API (400)."
    return "api_error", f"Erro da API: {type(exc).__name__}."
