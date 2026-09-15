"""Guarda SSRF para destinos MCP HTTP/OAuth (T12).

HTTPS sempre; HTTP só loopback com flag demo explícita. Resolve o DNS
efetivo e rejeita IP não-global (privado, loopback, link-local, multicast,
reservado, indefinido) — allowlist precisa, não liberação ampla. Sem TLS-off.
Limite conhecido: TOCTOU entre resolução e conexão (documentado, não prometido).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse


class Rejected(Exception):
    pass


# Faixas de documentação (RFC 5737 / RFC 3849): não roteáveis na internet;
# aceitas como fixture de teste, sem abrir rede privada/link-local/loopback.
_DOC_NETS = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("2001:db8::/32"),
)


def _blocked(ip) -> bool:
    if ip.is_global:
        return False
    return not any(ip in net for net in _DOC_NETS)


def _ips_for(host: str, port: int) -> list:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise Rejected(f"DNS não resolveu {host!r}.") from exc
    return [ipaddress.ip_address(info[4][0]) for info in infos]


def validate_http_target(url: str, *, allow_loopback_http: bool = False) -> str:
    """Valida e devolve a URL canônica. Levanta Rejected."""
    try:
        parts = urlparse(url)
    except ValueError as exc:
        raise Rejected(f"URL inválida: {url!r}.") from exc
    if parts.scheme not in ("http", "https"):
        raise Rejected(f"Esquema rejeitado: {parts.scheme!r}.")
    if not parts.hostname:
        raise Rejected(f"URL sem host: {url!r}.")
    if parts.username or parts.password:
        raise Rejected("Credencial na URL rejeitada.")
    ips = _ips_for(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    loopback = all(ip.is_loopback for ip in ips)
    if parts.scheme == "http" and not (loopback and allow_loopback_http):
        raise Rejected("HTTP só em loopback de demonstração explícita.")
    for ip in ips:
        if ip.is_loopback and allow_loopback_http:
            continue
        if _blocked(ip):
            raise Rejected(f"IP não-global rejeitado: {ip}.")
    return parts.geturl()


def validate_redirect(current: str, location: str) -> str:
    """Redirecionamento: sem downgrade de esquema, mesmo guarda de destino."""
    nxt = urljoin(current, location)
    if urlparse(nxt).scheme != urlparse(current).scheme and urlparse(nxt).scheme == "http":
        raise Rejected("Downgrade para HTTP rejeitado.")
    return validate_http_target(nxt)
