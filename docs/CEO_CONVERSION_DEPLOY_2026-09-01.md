# Conversion Agent Deploy — 2026-09-01

**Owner:** CEO / TOA. Agents convert. Humans do not book calls.

## Live (verified this cycle)
- https://getsincor.com/health — ready, a2a_inbound live_agents=1, 10 probation auctions
- Agent Card 200: https://getsincor.com/.well-known/agent-card.json
- Checkout: https://getsincor.com/buy and /products/starter ($297)
- Morpho treasury farm: **OFF**
- Founder extra ~$800 cash: **HOLD**
- On-chain treasury: ~$207 USDC **HOLD**

## What is deployed in-repo (this commit)
- Scout tasks: healthcare RCM + WebBuilder ICPs every 90m (public sources, no invented emails)
- Negotiator tasks: 3-touch → **/buy** every 60m; A2A external listing every 120m
- Sequences CTA patched: pay link, not calendar
- Cron still: `agents/runner.py --once` every 15m

## What still blocks SEND
Conversion **send** no-ops without:
1. `RESEND_API_KEY` (or equivalent) on the production runner host
2. `OUTREACH_ENABLED=true` `AUTONOMOUS_AGENTS=true` on that host
3. Auditor pass on each envelope in `agents/outbox/`
4. #188/#139 AXM fee `record_platform_fee_inflow` so a paid A2A call hits the ledger

Gmail connector on this operator: **mail service not enabled** — do not route sends through it.

## KPI (only these)
`sent`, `checkout_clicks`, `realized_tx_hash` to `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`

Meetings booked = 0 value.
