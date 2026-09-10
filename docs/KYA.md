# KYA stack — ship notes

Identity is the product. Live identity is `sincor2.kya_registry` mounted at `/v1/kya` (`kya_blueprint.py`). This drop adds the merkle quest, SLA **receipts**, SADAS, and Polyclaw **without replacing** `/v1/a2a/register` or `/v1/kya/list`.

## Money path

AXM first. USDC second. Card last. See `src/sincor2/kya/pricing.py`.

## Merkle quest (this PR)

On-chain verification is a keccak256 sorted-pair tree over the disperse recipient list. Not a stub wallet set.

- Build: `python scripts/kya_build_merkle.py --from-file recipients.txt --source axm-disperse`
- Manifest: `data/kya/airdrop_merkle.json` (`/data/` is gitignored — load on Railway via `KYA_AIRDROP_MANIFEST` or the data volume)
- Seed API: `POST /v1/quest/seed` requires `KYA_ADMIN_KEY` + header `X-KYA-Admin`
- Claim: `POST /v1/quest/claim` `{ wallet, agent_id, proof }` after KYA **verified**
- Identity lookup uses `kya_registry.get_by_agent` (live records)
- Reward: 15 AXM ledger credit — treasury settles; not an auto-transfer
- Do not publish a desk-replica root as the drop root

## Endpoints

Existing (do not fork):

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/kya/health` | registry snapshot |
| GET | `/v1/kya/directory` | same snapshot (static; must sit above `/<kya_id>`) |
| POST | `/v1/kya/list` | free listing |
| POST | `/v1/kya/bind` | principal + signature |
| POST | `/v1/kya/verify` | stake + live heartbeat → verified |
| POST | `/v1/kya/heartbeat` | 60s TTL |
| GET | `/v1/kya/lookup?wallet=` | pre-trade check (wallet only) |
| GET | `/v1/kya/agent/<id>` | by agent |
| POST | `/v1/kya/sla` | writes SLA onto the KYA record |

Additive in this PR:

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/kya/stack` | prices + quest stats + HOLD |
| POST | `/v1/sla/subscribe` | bind 5-min pings to kya_id |
| POST | `/v1/sla/ping` | epoch receipt |
| GET | `/v1/sla/receipts` | attestations |
| POST | `/v1/sadas/publish` | admin |
| POST | `/v1/sadas/subscribe` | paid token |
| GET | `/v1/sadas/scorecard` | public |
| GET | `/v1/sadas/feed` | gated unless token |
| POST | `/v1/polyclaw/day` | admin |
| GET | `/v1/polyclaw/scorecard` | vault gate |
| GET | `/v1/quest` | merkle stats |
| POST | `/v1/quest/seed` | admin — load disperse list |
| GET | `/v1/quest/eligibility?wallet=` | proof + root |
| POST | `/v1/quest/claim` | verified agent + merkle → ledger credit |

## Mount

`a2a_bootstrap.register_a2a` → `kya_bootstrap.mount_kya_stack` → `kya.blueprint.mount_kya`. Additive. Skips only if `kya_stack` is already registered. **Does not skip** just because Blueprint `kya` exists.

## Not in this drop

- Live Base writes (receipts are hashed; submit later with a signer)
- Stripe SKU (live /health: stripe not_configured)
- Vault deposits (HOLD)
- AXM fractional reserve
- NFT mint
