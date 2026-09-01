# Conversion 4h tick — 2026-09-01 ~12:30 UTC

KPI: sent=0 · checkout_clicks=unknown · realized_tx_hash=none

## Finding that matters
`POST /api/platform/checkout` with `plan_id=starter` is **price_unavailable** on production.
AXM (`0x4c3f…715a`) has no CoinGecko price and DexScreener `pairs: null`.
Only the $49 **report** plan quotes (500 AXM fixed). Sequences sell $297 Starter into a dead quote.

## Code this tick
USDC fallback for USD-priced human checkout when AXM spot is missing.
A2A quotes stay AXM-only (4.0000 AXM healthcare-credential-check, 500 bps to treasury).
Agent Card copy: AXM-only (was SINC or AXM).

## Not done (by design)
- No Morpho
- No founder $800
- No cold email (no RESEND_API_KEY on runner)
- agentpeering POST /api/agents = 401 (needs GitHub login / PAT)
- itinai submit still 404

Treasury live: ~$207.62 USDC + ~0.00063 ETH. 1e9 AXM in-wallet is unlisted inventory, not runway.
