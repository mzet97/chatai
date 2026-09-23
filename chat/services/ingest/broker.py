"""M4: fronteira Broker — publica mensagens com confirmação.

Local-lite usa `LocalBroker` (memória, confirm imediato). No homelab, um
`RabbitBroker` liga a mesma tabela OutboxMessage (§15) sem mudar o
dispatcher nem o consumidor; RabbitMQ indisponível mantém os jobs na
outbox/fila lógica e a UI indica atraso (matriz de degradação).
"""

from __future__ import annotations


class Broker:
    """Contrato mínimo: publica e confirma (publisher confirm)."""

    def publish(self, topic: str, payload: dict) -> str:
        raise NotImplementedError


class LocalBroker(Broker):
    """Broker em memória para o perfil local-lite e para testes."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []
        self._seq = 0

    def publish(self, topic: str, payload: dict) -> str:
        self._seq += 1
        receipt = f"local-{self._seq}"
        self.published.append((topic, dict(payload)))
        return receipt
