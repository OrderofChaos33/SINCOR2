# SINCOR A2A Production Checklist — Massive Agent Inflow

**Status as of 2026-08-17 audit:** Discovery surfaces were **offline** on production (`mvp_app.py` did not register `A2ARouter`). Static `agent.json` only.

## Non-negotiable success path

External A2A v1.0.1 agent must be able to:

1. `GET https://getsincor.com/.well-known/agent-card.json` → full card (43+ skills, schemas, AXM pricing, freeQuota)
2. Register via documented path
3. Quote a skill
4. Pay (or free quota) with AXM to treasury `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`
5. Execute skill + receive result + proof-of-settlement (50% burn / 50% treasury)

## Critical path (do first)

### 1. Wire blueprint into production entrypoint

In `src/sincor2/mvp_app.py` (production is `gunicorn sincor2.mvp_app:app`), add **immediately after** `app = Flask(...)` and security middleware setup:

```python
# A2A discovery + JSON-RPC (critical for external agent inflow)
try:
    from sincor2.a2a_bootstrap import register_a2a
    if register_a2a(app):
        logger.info("[A2A] discovery surfaces registered")
    else:
        logger.error("[A2A] registration failed — external agents cannot discover")
except Exception as e:
    logger.error("[A2A] bootstrap error: %s", e)
```

**Conflict note:** mvp_app currently serves a static `/.well-known/agent.json`. After registering the blueprint, either:
- Remove the static `@app.route('/.well-known/agent.json')` handler, or
- Register the blueprint *before* that route and leave the dynamic handler (blueprint routes are preferred if registered first in some Flask versions — safest is to delete the static route).

### 2. Railway / production env vars (required)

```
PLATFORM_URL=https://getsincor.com
A2A_PRIMARY_TOKEN=AXIOM
AXIOM_CONTRACT_ADDRESS=0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822
TREASURY_ADDRESS=0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac
BASE_RPC_URL=https://mainnet.base.org   # or Alchemy/QuickNode Base URL
BASE_CHAIN_ID=8453
A2A_TASK_STORE=redis                    # required for multi-worker Gunicorn
REDIS_URL=redis://...                   # Railway Redis or Upstash
```

Without `BASE_RPC_URL`, PaymentVerifier cannot call `eth_getTransactionReceipt` → paid path fails.
Without Redis task store, multi-worker Gunicorn fragments / loses tasks.

### 3. Post-deploy smoke (must pass before any directory listing)

```bash
curl -sS https://getsincor.com/.well-known/agent-card.json | jq '.name, (.skills|length), .supportedInterfaces'
curl -sS "https://getsincor.com/api/a2a/quote?skill_id=lead-enrichment&caller_id=smoke" | jq .
curl -sS https://getsincor.com/api/a2a/agents | jq '.primary_token, .chain_id, (.agents|length)'
curl -sS https://getsincor.com/health | jq '.checks.base_rpc'
```

Expected:
- agent-card: name present, skills ≥ 15, supportedInterfaces with JSONRPC + protocolVersion 1.0.1
- quote: pay_to = treasury, primary_token AXIOM (or free_quota path)
- agents: primary_token AXIOM, chain_id 8453
- health base_rpc: ready (not `not_configured`)

## Track status

| Track | Status | Notes |
|-------|--------|-------|
| A Discovery + blueprint | **BLOCKED until mvp_app one-liner** | Code exists in a2a_integration.py; not registered on production |
| B Token/settlement AXM primary | Code ready; default was SINC — set env + change default |
| C Registration endpoint | Exists at POST /api/marketplace/register (SINC stake gated) |
| D Adapters + demos | Partial (examples/a2a_loop_demo.py) |
| E Directory submissions | Blocked until card is green |
| F Observability + Redis | Logging present; Redis task store not yet wired in a2a_integration |

## Settlement economics (AXM)

- 50% burn → `0x000000000000000000000000000000000000dEaD`
- 50% treasury → `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`
- Platform fee path kept in SettlementCoordinator (fee-only treasury inflow accounting)

## Files that already implement production logic

- `src/sincor2/a2a_integration.py` — full A2ARouter, skills, PaymentVerifier, price engine, reputation, free quota, settle proof
- `src/sincor2/blueprints/marketplace.py` — register, routing, settlement, leaderboard
- `src/sincor2/a2a_bootstrap.py` — this PR — one-call registration helper

**Do not announce or submit to a2a directories until agent-card.json is 200 with full skills array.**
