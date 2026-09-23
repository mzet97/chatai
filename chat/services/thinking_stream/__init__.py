"""M2 — streaming de pensamento (estudo + preparação, sem conflito com M1).

Por que um pacote novo (`thinking_stream/`) em vez de `thinking/*.py`?
`chat/services/thinking.py` já existe como módulo (TV-1: composição dos args
oficiais). Um pacote `thinking/` colidiria com esse módulo no import. Este
pacote sidecar cobre só a RESPOSTA do pensamento, sem tocar em
`generation.py` nem `models.py`:

- `events`: protocolo SSE thinking_started/delta/completed (+ redacted) e
  extração dos deltas de pensamento do stream bruto do SDK.
- `store`: persistência separada — log de protocolo (append-only, com
  signatures, nunca exibido) vs projeção (resumo p/ o painel
  "Resumo do pensamento").
- `replay`: reconstrução de blocos p/ continuação/retomada, com tool_result.

Integração futura (M1 dono dos arquivos): `generation._iter_stream_text`
passa a rotear deltas de pensamento p/ `events.iter_thinking_events`;
`execute_run` emite os eventos deste pacote via `_emit` e persiste via
`store.to_snapshot`; o painel didático (`api_runs.run_detail`) expõe a
projeção; `chat.js` renderiza o painel "Resumo do pensamento".
"""

from chat.services.thinking_stream.events import (
    EVENT_COMPLETED,
    EVENT_DELTA,
    EVENT_REDACTED,
    EVENT_STARTED,
    iter_thinking_events,
    make_completed,
    make_delta,
    make_redacted,
    make_started,
    with_envelope,
)
from chat.services.thinking_stream.replay import (
    build_continuation_blocks,
    replay_messages,
)
from chat.services.thinking_stream.store import (
    PROTOCOL_VERSION,
    SNAPSHOT_KEY,
    append_delta,
    complete,
    from_snapshot,
    mark_redacted,
    new_log,
    project_panel,
    redact_for_display,
    to_snapshot,
)

__all__ = [
    "EVENT_COMPLETED",
    "EVENT_DELTA",
    "EVENT_REDACTED",
    "EVENT_STARTED",
    "PROTOCOL_VERSION",
    "SNAPSHOT_KEY",
    "append_delta",
    "build_continuation_blocks",
    "complete",
    "from_snapshot",
    "iter_thinking_events",
    "make_completed",
    "make_delta",
    "make_redacted",
    "make_started",
    "mark_redacted",
    "new_log",
    "project_panel",
    "redact_for_display",
    "replay_messages",
    "to_snapshot",
    "with_envelope",
]
