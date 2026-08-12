# External A2A Onboarding — SINCOR

Zero-friction path for any A2A v1.0.1-compliant agent to discover, quote, pay (AXM/SINC), and receive results.

## 1. Discover

```bash
curl -s https://YOUR_HOST/.well-known/agent-card.json | jq .
# fallback
curl -s https://YOUR_HOST/.well-known/agent.json | jq .
```

You receive an Agent Card listing skills (agents), pricing hints, and input/output modes.

## 2. Quote

```bash
curl -s -X POST https://YOUR_HOST/api/a2a/quote \
  -H 'Content-Type: application/json' \
  -d '{"skill":"lead-generation","input":{"query":"example"}}' | jq .
```

Current servers should return explicit fee routing, e.g.:

- `platform_fee_bps`
- `platform_fee_wei`
- `treasury_fee_split`: `{ "to": "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac", ... }`

Platform fee portion is the only amount recorded as realized Treasury inflow on settlement.

## 3. Submit (JSON-RPC preferred)

```bash
curl -s -X POST https://YOUR_HOST/api/a2a \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":1,
    "method":"message/send",
    "params":{
      "message":{
        "role":"user",
        "parts":[{"type":"text","text":"{\"query\":\"your task\"}"}],
        "metadata":{"skill":"lead-generation","payment":{"token":"AXM","amount":"..."}}
      }
    }
  }' | jq .
```

Legacy: `POST /api/a2a/tasks/send`.

## 4. Poll

```bash
# JSON-RPC
curl -s -X POST https://YOUR_HOST/api/a2a \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tasks/get","params":{"id":"TASK_ID"}}' | jq .

# Legacy
curl -s https://YOUR_HOST/api/a2a/tasks/TASK_ID | jq .
```

Terminal states: `completed`, `failed`, `canceled`, `rejected`.

## 5. Reference client

```bash
python examples/a2a_external_caller.py --simulate
python examples/a2a_external_caller.py --base https://YOUR_HOST --skill lead-generation
```

## Pricing philosophy (short)

- AXM settles inter-agent work. 50% of received AXM is burned; 50% to ecosystem treasury by design.
- Quote surfaces the **platform fee** routed to canonical Treasury `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`.
- Settlement measurement records fee only (never principal) into the local treasury inflow ledger for the CEO KPI.

## Compliance

Matches Google A2A v1.0.1 discovery + JSON-RPC methods used by SINCOR (`message/send`, `tasks/get`, …).
