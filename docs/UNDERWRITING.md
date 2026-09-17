# Agent Underwriting Runtime

SINCOR underwrites agent mandates on Base. Identity is ERC-8004. Settlement is x402 / AXM. This module is the policy decision.

## Wire (do not invent REST)

- JSON-RPC: `POST https://getsincor.com/api/a2a` method `message/send`
- `GET /api/a2a/message/send` 404 is expected
- Underwriting HTTP:
  - `POST /v1/agents/register`
  - `POST /v1/mandates/underwrite`
  - `POST /v1/mandates/{envelope_id}/settle`
  - `POST /v1/agents/{agent_id}/revoke`
  - `GET /v1/receipts/{receipt_id}`
  - `GET /v1/underwriting/health`

## Addresses

| What | Value |
|---|---|
| Chain | Base 8453 |
| ERC-8004 Identity | `0x8004A169FB4a3325136EB29fA0ceB6D2e539a432` |
| AXM | `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a` |
| Treasury | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` |

## Fees (v0)

- Verification: 2 AXM
- Underwriting: 50 bps of settled notional
- 50% of underwriting fee burned, 50% to treasury

## Score v0 hard denies

revoked · invalid principal · invalid agent wallet · burn address · outside window · invalid notional · exceeds cap · wash loop (≥20 same payer→treasury)

Missing ERC-8004 is a penalty, not a deny.

## Operator

```
python scripts/erc8004_register.py
python scripts/probe_paid_mandate.py
python -m unittest tests.test_underwriting_engine
```

Existing KYA (`/v1/kya/*`, `/v1/a2a/register`) stays. Underwriting sits on top.
