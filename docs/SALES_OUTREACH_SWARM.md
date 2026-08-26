# Sales Outreach Swarm — Autonomous (Agents Only)

**Status:** ARMED 2026-08-25. Human meeting-booking path deleted.

## Rule
No humans book calls. Agents discover, contact, quote, and close to on-chain checkout. Fulfillment is agent-side after payment lands in treasury `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`.

## Close path (only)
1. Scout fetches ICP leads (Yelp / Places / WebBuilder no-site).
2. Outreach agent emails a **pay link**, not a calendar link.
3. Buyer pays SINC/AXM at https://getsincor.com/buy (or A2A quote).
4. Payment webhook / A2A settlement triggers report or site staging.
5. Follow-up is agent Touch-2/3. STOP honored. No “reply to Court.”

## Runtime flags
- `AUTONOMOUS_AGENTS=true` (master)
- `OUTREACH_ENABLED=true` (default on)
- `CONTENT_AGENT_ENABLED=true`
- `RECURSIVE_OPTIMIZER_ENABLED=true`
- `RESEND_API_KEY` + `YELP_API_KEY` required or cycle no-ops honestly

## Metrics that count
`sent`, `checkout_clicks` (utm), `realized_tx_hash` to treasury. Replies and “meetings booked” do not count.
