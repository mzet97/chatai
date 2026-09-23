"""Execução durável M2 (Rev1.1 §6–§8): diário de eventos da raiz.

`GenerationRun` (chat/models.py) é a raiz; aqui vive só o diário
(`RunJournalEvent`, distinto do `RunEvent` de AgentRun em models_agents).
Sem banco paralelo. Seq única por run, monotônica, só projeção liberada
(sem segredos, assinaturas ou contextos completos).
"""

import uuid

from django.db import models


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class RunJournalEvent(models.Model):
    """Evento confirmado do diário; SSE lê daqui com cursor, nunca reexecuta."""

    uuid = models.UUIDField(default=new_uuid, unique=True, editable=False)
    run = models.ForeignKey("chat.GenerationRun", on_delete=models.CASCADE, related_name="journal")
    seq = models.PositiveIntegerField()
    kind = models.CharField(max_length=60)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["seq"]
        constraints = [models.UniqueConstraint(fields=["run", "seq"], name="unique_seq_per_run")]
        indexes = [models.Index(fields=["run", "seq"])]

    def __str__(self) -> str:
        return f"run={self.run_id} seq={self.seq} {self.kind}"
