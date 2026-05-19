# Post-Launch Liquidity Acceleration Playbook

**Starts:** Day +1 after V4 pool graduation (~2026-05-20)
**Goal:** Multiply effective liquidity 5–10× and recruit external LPs without (a) spending treasury capital and (b) crossing the wash-trading line.
**Anchored to:** [[launch_tactics_legal_line]] memory + design spec §11 Phase 3.

This is the **legal** mechanism stack for "max liquidity fast." Everything below either uses real external capital, transparent self-MM with disclosure, or amplifies existing real LP depth via concentrated-range positions. Nothing here creates false appearance of liquidity — that's the line we don't cross.

---

## Sequencing overview

| Day | Mechanism | Effort | New capital needed | Estimated depth lift |
|---|---|---|---|---|
| +0 | V4 LP burned at graduation | done in graduation tx | $0 (buyers paid) | Baseline (~$1.2k ETH-paired) |
| +1 to +3 | **Bunni concentrated LP** on top of burned base LP | 1-2h scripting | $0 from treasury; redeploys existing pool ownership | 5-10× effective depth in active range |
| +3 to +7 | **Liquidity mining v0** — SINC emissions to real LPs | 4-6h smart contract or Merkle script | 0-2M SINC from treasury reserve over 90 days | Recruits external capital |
| +7 to +14 | **Hummingbot two-sided MM, disclosed** | 2-4h setup | $200-500 in seed quote currency | Tighter spreads, smoother price discovery |
| +14 to +30 | **Cross-chain expansion** — Optimism via Across, then Arbitrum | 8-12h | bridging gas + LP capital from earned fees | New audiences, multiplied surface area |

If launch volume is weak in the first 72 hours, jump to Bunni + liquidity mining v0 immediately. Cross-chain waits until Base baseline is healthy (≥$10k daily volume sustained 7 days).

---

## Mechanism 1 — Bunni concentrated LP (Day +1–3)

### What it is
Bunni Protocol (https://bunni.pro) lets LPs deposit a Uniswap v4 LP position into a concentrated tick range, earning the same fees a regular v4 LP would but with 5-10× capital efficiency inside the active price band. **Not** a fork of liquidity — it manages the same underlying v4 pool, just routes capital into a tighter range.

### Why it's legal
- Standard DeFi LP mechanic
- Same `PositionManager.modifyLiquidity()` call any LP makes
- All capital is real
- Position is on-chain, auditable on Basescan
- No fake-appearance issue

### Why it works for SINC specifically
The burned V4 LP at graduation is full-range (price 0 to ∞). That's safe but capital-inefficient. A Bunni position at e.g. ±20% of current price uses the same dollars to provide 5× the depth where trades actually happen. The burned LP earns the fees of a full-range position; a Bunni overlay earns ~5× more fees per dollar for the LP holder.

### How to deploy

1. **Wait for V4 pool to stabilize** — at least 24h of trading post-graduation, ideally 48h. You want a reliable price reference before defining the tight range.
2. **Open Bunni position via their UI** at https://bunni.pro (Base section) or via direct contract call.
3. **Tight range:** `currentPrice × 0.85` to `currentPrice × 1.15` (±15%). If you want more aggressive: ±10%. More conservative: ±25%.
4. **Capital source:** the 20% ETH treasury cut from graduation. At threshold this is ~0.1 ETH. You pair it with treasury's remaining SINC reserve.
5. **Document the position:**
   - Tx hash to a `data/launch_liquidity_log.jsonl` entry
   - Cross-link from `/sinc` page ("Concentrated LP at $X-Y range — verify on Basescan: [link]")
   - Dune dashboard query showing the position's TVL + fees earned

### Risk
Concentrated positions go inactive (earn $0 fees) if price exits the range. Mitigation: monitor daily; if price approaches a band, re-tick by withdrawing and redepositing at the new range. This is a 10-min weekly task.

### What it is NOT
- Not "double the liquidity" — same dollars, just better-placed
- Not a guarantee of more volume — volume comes from real buy interest
- Not wash trading — doesn't create any artificial swaps

---

## Mechanism 2 — Liquidity Mining v0 (Day +3–7)

### What it is
Pay external LPs (real people providing real LP positions) in SINC emissions for adding depth to the canonical V4 SINC/WETH pool. Transparent, capped, time-limited.

### Why it's legal
- Standard DeFi mechanic (Uniswap, Aave, Compound all run mining programs)
- All participants are real third parties providing real capital
- Emission schedule is public + capped
- Token economics published; no implicit return promise

### Program design (v0 — minimum viable)

| Parameter | Value | Notes |
|---|---|---|
| Total emission | 2,000,000 SINC | 2% of supply, from treasury reserve (treasury holds 35M after Phase 1 graduation) |
| Duration | 90 days | Can extend with v1 if successful |
| Eligibility | LP positions in canonical V4 pool with WETH pair | Both burned-LP-style and concentrated-LP-style positions count, *pro rata by liquidity provided* |
| Distribution method | Merkle drop weekly | User claims via signed Merkle proof. No staking contract required for v0. |
| Snapshot frequency | Every 7 days at 00:00 UTC | Snapshot Sunday-night Base block; distribute Monday |
| Claim window per week | 14 days | After 14 days unclaimed → rolls forward to next week's pool |
| Pause / cancel authority | None (no admin) | OR multi-sig with 7-day timelock; user decides |
| Audit | Self-audit week 1, optional CertiK Skynet scan after week 2 | Free initial scan if contract code is small |

### How agents help

Per `[[project_sinc_axiom_relaunch]]` Director archetype:
- **Scout** identifies LP candidates from Dune queries (wallets that LP on similar Base tokens)
- **Synthesizer** drafts the mining-program announcement post (must pass `check_crypto_promotion()` — no "easy yield" framing, no APR promises, just "X SINC distributed weekly to LPs pro rata")
- **Builder** writes the Merkle generation script (Python, daily on cron, output published to IPFS pinned for transparency)
- **Auditor** verifies each week's Merkle root against the snapshot before the distribution post

### Implementation cost
- Merkle generator script: ~150 lines Python
- Claim contract: ~50 lines Solidity (OZ's MerkleDistributor) — already audited, just deploy
- Total: ~6 hours from spec to deployed
- Treasury cost: 2M SINC over 90 days = 22,200 SINC/week ≈ ~$22-220/week in current price terms

### Risk
- Bot-LPs that game the system by JIT-providing liquidity around snapshot blocks. Mitigation: snapshot at randomized block within a 6-hour window each Sunday.
- Sybil attack (one person, many wallets). Mitigation: not worth fighting at this scale; v1 can add stake-time weighting.

---

## Mechanism 3 — Hummingbot transparent two-sided MM (Day +7+)

### What it is
[Hummingbot](https://hummingbot.org) is open-source market-making software. You run it on your own infrastructure, configure a strategy (e.g., pure market-making with ±0.5% spread), and it places real buy + sell limit orders into the V4 pool. Both sides serve real users.

### Why it's NOT wash trading
- Orders are placed at *non-zero spread* — every executed trade has a real counter-party
- All orders rest on the order book; no self-matched fills
- The MM strategy is *published* (we link the Hummingbot config or describe the strategy on the /sinc page)
- It's the same activity any pro market-maker does on a CEX — moved on-chain

### Why it matters for SINC
- Tightens spreads (better UX for buyers)
- Provides depth on both sides when natural buyers are sparse
- Reduces price-impact volatility

### Cost
- $200-500 in seed quote currency (split between SINC and WETH)
- A small VPS to run Hummingbot 24/7 (~$5-10/month)
- Configuration time: 2-4 hours

### Disclosure
Must add to `/sinc` page:
> "SINC liquidity is provided in part by a transparent market-making bot operated by the SINCOR team. Strategy: pure market-making with [X]% spread on canonical V4 pool. Wallet: 0x... Verify all activity on Basescan."

That sentence is the difference between "transparent self-MM" (legal) and "wash trading" (illegal). Don't ship the bot without the disclosure.

---

## Mechanism 4 — Cross-chain expansion (Day +14+)

### Sequencing
- **Base** (canonical) → seeded
- **Optimism** (day +14 if Base has ≥$10k daily volume sustained 7 days)
- **Arbitrum** (day +30 if Optimism reaches similar threshold)

### Why this order
Base + Optimism + Arbitrum are all OP-stack L2s sharing the Coinbase ecosystem audience. They convert well together. Skip non-OP chains (Polygon, BNB) for now — different audiences, more bridging risk.

### Mechanism
Use **Across** (https://across.to) or **LayerZero OFT** to bridge SINC across chains. Across is simpler; LayerZero OFT is more flexible (lets SINC mint/burn natively on each chain via OFT contracts).

For SINC specifically: since the canonical contract is decimals=8 and small, **lock-and-mint via Across** is cleaner than re-deploying with LayerZero OFT. No need to fork the contract; canonical stays on Base.

### Real liquidity on each chain
**Critical:** don't just bridge SINC and hope for it. Deploy a fresh V4 pool on each target chain with real ETH paired with SINC. Capital source:
- Treasury earned fees from Base
- A small (~$500) seed from the liquidity-mining accumulation
- Concentrated LP via Bunni on each chain too

### What this is NOT
- Not "doubling the liquidity" by recycling the same dollars — each chain has its own real LP
- Not flash-loan magic — actual locked SINC on Base + minted SINC on target chain, all auditable

---

## What we explicitly REFUSE to do

Per [[launch_tactics_legal_line]] memory:

| Tactic | Why refused |
|---|---|
| Bot-driven buy waves to fake organic demand | Wash trading. CFTC Commodity Exchange Act, DOJ wire fraud (Eisenberg precedent), MiCA Art 91 |
| Agent-to-agent buy/sell cycling | Same. The blockchain doesn't care that it's agents — it sees self-matched fills |
| Flash-loan "liquidity doubling" — making $X appear as $10X within a single tx | Wash trading by another name. Crypto Twitter loves to claim this is novel; DOJ doesn't |
| Coordinated buy calls timed for price impact | Securities-fraud exposure (SEC unregistered-promotion), CFTC manipulation |
| Sockpuppet accounts shilling SINC across crypto subreddits | FTC fake-testimonial; platform TOS violations; reputational suicide |
| Cross-chain "remixing" that obscures source of capital | Money-laundering structuring exposure (BSA/FinCEN) |

If any of these tactics surface in agent drafts via the content engine, `check_crypto_promotion()` blocks them. If they surface in code, I won't write them.

---

## Pre-launch deliverables (do tonight)

- [ ] **Decide:** what's the SINC reserve for liquidity mining? Recommended: 2M SINC from the 35M treasury holding.
- [ ] **Decide:** Bunni opening range (±10%, ±15%, ±25%). Recommended: ±15%.
- [ ] **Bookmark:** Bunni Base UI, Hummingbot docs, Across bridge.
- [ ] **Add disclosure stub to `/sinc` page** for the eventual Hummingbot MM. Better to ship the disclosure copy now than scramble when MM goes live.

---

## What lands on Day +1 vs later

| Day | Ship | Why this day |
|---|---|---|
| +1 | Bunni concentrated LP open (if pool has 24h of price data) | Doesn't require building anything new; uses Bunni UI |
| +3 | Liquidity mining program announcement post | Lets the Merkle infrastructure ship without rushing |
| +5 | First liquidity mining Merkle distribution | Snapshot Sunday, distribute Monday |
| +7 | Hummingbot MM live with disclosure | After we see the natural spread shape from 7 days of trading |
| +14 | Optimism expansion (conditional on $10k daily volume on Base) | Don't expand off a sluggish base |
| +30 | Arbitrum expansion (conditional on Optimism reaching $10k daily) | Same gate |

---

## Connection to project

- Replaces spec §11 items 1-2 (Morpho lending, Bunni LP) for short-horizon execution
- Cross-refs [[project_sinc_axiom_relaunch]] for canonical contract address `0x9C8cd8d3961F445D653713dE65C6578bE11668e7`
- Adheres to [[signing_authority]] — all on-chain actions signed by user, not me. Merkle generation scripts produce *proposals*; user signs the deploy and the weekly root-update txs.
