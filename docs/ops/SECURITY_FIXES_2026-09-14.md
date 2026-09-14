# Security fixes 2026-09-14

- Agent ids must match `E-<name>-NN`. Paths cannot leave `agents/`.
- `POST /api/a2a/heartbeat` requires `X-Sincor-Heartbeat` = `AGENT_HEARTBEAT_TOKEN`. Missing token → 401 (fail closed).
- `/api/sku/quote` ignores client `axm_spot`.
- `authorize(agent_id, tool)` refuses WardrobeDraft/Hatch/KeyPending/Suspended.
- Set `AGENT_KILL_ALL=1` or `AGENT_KILL_E_sirius_08=1` to halt.
- Draft agents cannot execute kernel tools.
