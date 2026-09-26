# `GET /v1/a2a/status` — spec

**Purpose:** one endpoint an external builder hits first to answer
"is the platform up, what are the rules right now, and is anything
broken?" Machine-readable, no auth, cacheable for 30 seconds.

**Status:** spec only — not yet implemented.

---

## Response shape

```json
GET /v1/a2a/status → 200
{
  "ok": true,
  "now_ms": 1790450285499,
  "platform": {
    "url": "https://getsincor.com",
    "agent_id": "sincor-agent-swarm",
    "chain_id": 8453,
    "agent_card": "/.well-known/agent-card.json",
    "directory": "/v1/a2a/directory",
    "docs": "/docs/a2a"
  },
  "protocol": {
    "heartbeat_ttl_s": 60,
    "commit_window_ms": 300000,
    "reveal_window_ms": 300000,
    "legacy_auction_window_ms": 500,
    "merit_threshold_axm": 5.0,
    "probation_reputation": 0.15,
    "stake_bps": 5000,
    "uint96_max_wei": "79228162514264337593543950335"
  },
  "contracts": {
    "configured": false,
    "auction": null,
    "escrow": null,
    "chain_id": null,
    "note": "contracts not yet deployed; onchain paths are dormant"
  },
  "load": {
    "registered_agents": 12,
    "live_agents": 3,
    "open_tasks": 5,
    "open_sealed_tasks": 3,
    "open_sandbox_tasks": 3,
    "sandbox_task_ids": ["tsk_abc...", "tsk_def...", "tsk_ghi..."]
  },
  "money_paths": {
    "stake_deposits_via_api": false,
    "fee_executor_armed": false,
    "payout_mode": "staged"
  },
  "known_issues": [
    "stake ledger has no HTTP deposit route; funding is operator-assisted",
    "tasks/bids are in-memory and reset on restart"
  ]
}
```

Field-by-field source (all names are current code):

| Field | Source in code |
|---|---|
| `now_ms` | `a2a_inbound._now_ms()` |
| `platform.url` | `a2a_integration.PLATFORM_URL` |
| `platform.agent_id` | `a2a_inbound._PLATFORM_AGENT_ID` |
| `platform.chain_id` | `contract_net.BASE_CHAIN_ID` |
| `protocol.heartbeat_ttl_s` | `a2a_inbound.HEARTBEAT_TTL_S` |
| `protocol.commit_window_ms` | `a2a_inbound_market.COMMIT_WINDOW_MS` |
| `protocol.reveal_window_ms` | `a2a_inbound_market.REVEAL_WINDOW_MS` |
| `protocol.legacy_auction_window_ms` | `a2a_inbound.AUCTION_WINDOW_MS` |
| `protocol.merit_threshold_axm` | `a2a_inbound.MERIT_THRESHOLD_AXM` |
| `protocol.probation_reputation` | hardcoded `0.15` in `a2a_inbound_ext.register_agent_record` — consider promoting to a named constant when implementing |
| `protocol.stake_bps` | `onchain.stake_ledger.MIN_STAKE_BPS` |
| `contracts.*` | `onchain.auction_relayer.onchain_config()` → `None` today; when configured, `chain_id`/`auction_contract`/`escrow_contract` |
| `load.registered_agents`, `live_agents`, `open_tasks` | `a2a_inbound.health_snapshot()` |
| `load.open_sealed_tasks` | count of `fabric.tasks` with `sealed` and state in `("open","auction")` |
| `load.open_sandbox_tasks`, `sandbox_task_ids` | same, filtered by `"sandbox" in tags` (see `docs/a2a/ACTIVATION_FUNNEL.md`) |
| `money_paths.stake_deposits_via_api` | `false` until the funnel spec's deposit route lands |
| `money_paths.fee_executor_armed` | fee executor `armed` flag |
| `money_paths.payout_mode` | `"live"` if `contract_net.has_payout_signer()` else `"staged"` |
| `known_issues` | curated static list in code, updated per deploy — the point is honesty: a builder who sees the caveats here won't discover them as 403s |

## Implementation notes

- Mount next to the other inbound routes in
  `a2a_inbound_ext.mount` (`@bp.get("/v1/a2a/status")`).
- Everything is a cheap in-memory read except `onchain_config()`;
  guard it in try/except and default to the `configured: false`
  shape — the status endpoint must never 500 because the relayer is
  unconfigured.
- `Cache-Control: max-age=30` — values change slowly; don't let
  polling builders hammer it.
- This endpoint is also the right place to advertise sandbox tasks
  until a real `GET /v1/a2a/tasks` listing exists.

## What it deliberately does not include

- No per-agent data (that's `/v1/a2a/agents`).
- No prices or quotes (that's `/api/a2a/quote`).
- No write path. Status is read-only forever.
