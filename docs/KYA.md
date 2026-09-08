# KYA stack — ship notes

Identity is the product. SLA, SADAS, Polyclaw receipts, and the airdrop quest hang off `kya_id`.

## Money path

AXM first. USDC second. Card last. See `src/sincor2/kya/pricing.py`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/kya/health` | prices + counts |
| POST | `/v1/kya/list` | free listing |
| POST | `/v1/kya/verify` | stake + fee → verified |
| GET | `/v1/kya/lookup?agent_id=` | pre-trade check |
| GET | `/v1/kya/directory?verified=1` | public registry |
| POST | `/v1/sla/subscribe` | bind 5-min pings to kya_id |
| POST | `/v1/sla/ping` | operator or cron |
| GET | `/v1/sla/receipts` | epoch attestations |
| POST | `/v1/sadas/publish` | signal |
| POST | `/v1/sadas/subscribe` | paid token |
| GET | `/v1/sadas/scorecard` | public track record |
| GET | `/v1/sadas/feed` | gated unless token |
| POST | `/v1/polyclaw/day` | daily scorecard row |
| GET | `/v1/polyclaw/scorecard` | vault gate |
| POST | `/v1/quest/seed` | load airdrop wallets |
| POST | `/v1/quest/claim` | verify agent → ledger credit |

## Mount

`a2a_bootstrap.register_a2a` calls `mount_kya(app)`. Additive. Does not replace `/v1/a2a/register`.

## Not in this drop

- Live Base writes (receipts are hashed; submit later with a signer)
- Stripe SKU
- Vault deposits
- AXM fractional reserve
- NFT mint
