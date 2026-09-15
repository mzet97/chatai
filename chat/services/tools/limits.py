"""Limites do ciclo de ferramentas (centralizados, T3)."""

MAX_MODEL_STEPS = 6
MAX_INVOCATIONS = 8
TOOL_TIMEOUT_S = 20
ACTIVE_BUDGET_S = 180  # exclui espera humana (pausas não consomem)
MAX_TOOLS_EXPOSED = 20
MAX_RESULT_BYTES = 32 * 1024
