# A2A production checklist

Live platform: `https://getsincor.com`  
Gunicorn entry: `sincor2.mvp_app:app`  
Wiring: `register_a2a(app)` in `src/sincor2/a2a_bootstrap.py` (idempotent; owns discovery).

## Canonical addresses (do not substitute)

| Role | Address |
|---|---|
| AXM (sole new settlement) | `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a` |
| Treasury | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` |
| SINC (8-dec residual) | `0xe1D836087F6573b665d25CE088793E916D7892f8` |

**Stale — never use:** `0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822` (dead PumpClawToken).

## Discovery smoke (must be 200)

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://getsincor.com/.well-known/agent-card.json
curl -sS -o /dev/null -w '%{http_code}\n' https://getsincor.com/.well-known/agent.json
curl -sS -o /dev/null -w '%{http_code}\n' https://getsincor.com/api/a2a/agents
curl -sS https://getsincor.com/api/a2a/quote?skill_id=lead-enrichment
curl -sS -o /dev/null -w '%{http_code}\n' https://getsincor.com/docs/a2a
```

Agent Card must include top-level `protocolVersion`, `url`, `preferredTransport=JSONRPC`.

## Railway env (required)

```
A2A_PRIMARY_TOKEN=AXIOM
AXIOM_CONTRACT_ADDRESS=0x4c3fb66f14fbaa2088c9ae91017ba770da53715a
TREASURY_ADDRESS=0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac
PLATFORM_URL=https://getsincor.com
BASE_RPC_URL=
A2A_TASK_STORE=redis
REDIS_URL=
```

Never set `EXECUTE_LIVE=1` in committed files.

## Checkout + settlement (closed in this cycle)

1. Discovery 200.
2. Quote exposes `treasury_fee_split` / `platform_fee_*`.
3. Settlement success records `record_platform_fee_inflow(..., projected=False, tx_hash=...)` even without Flask platform state. Simulated and free-quota paths write zero realized events.
4. Non-AXM quotes rejected on the canonical contract.
5. Human checkout (`/api/platform/checkout`) is idempotent by `idempotency_key` (or wallet+plan+hour), claims a verifying lock before RPC, retries Base RPC with public fallbacks, auto-expires past `expires_at`, and records a 5% platform fee on verified fills.

## External caller

`examples/external_agent_registration.sh` and `examples/EXTERNAL_A2A_ONBOARDING.md`.
