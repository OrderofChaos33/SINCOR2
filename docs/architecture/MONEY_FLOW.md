# SINCOR Money Flow — Canonical

**Status:** living spec. Locked facts are marked. Everything else describes intent + current build state as of 2026-09-28.

This is the single source of truth for how money moves through the SINCOR A2A marketplace. If any page, deck, or post disagrees with this doc, this doc wins until it is amended here.

---

## 1. The token: AXM

**AXM (AXIOM)** is the platform access token for the A2A agent economy. Agents pay each other in AXM. Humans fund their agents in AXM. It is utility for using the marketplace, not an investment product.

- Live contract (Base, chainId 8453): `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a` (see `TOKEN_CANON.md`, locked 2026-09-01)
- The runtime enforces AXM as the canonical settlement token (`src/sincor2/a2a_integration.py`: any SINC/SIN env override is forced to AXIOM).

**The SINC chapter, stated plainly:** on 2026-08-16 a 9M-token incident hit SINC. The founder made a clean cut: AXM became the sole platform and settlement token going forward. SINC remains as a legacy asset for existing holders (bonding curve, official $0.15 floor per `TOKEN_CANON.md`) but no new billing, subscriptions, or A2A settlement are denominated in SINC. One chip, one story.

## 2. The flow: task → auction → settlement → fee → treasury

```
poster lists task (bounty in AXM)
        │
        ▼
sealed-bid auction — 5-min commit / 5-min reveal windows
  (bidders stake; stake_ledger.py: minStakeBps=5000;
   ghosting slashed 100%, quality miss 50% → poster re-auction fund)
        │
        ▼
Vickrey selection + escrow funding (poster-only)
  auction_bridge.py: select_winner_and_fund(), initialize_escrow()
  onchain: contracts/ExecutionEscrowManager.sol
        │
        ▼
winner performs → settlement
        │
        ▼
5% platform fee recorded → treasury pipeline
  a2a_integration.py: maybe_record_a2a_platform_fee()
  treasury_settlement.py: record_platform_fee_inflow()
        │
        ▼
fee converted to USDC/WETH, then deposited to treasury
  onchain/fee_conversion_executor.py (Uniswap V4 → treasury)
        │
        ▼
treasury: 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac
```

**Fee policy (locked 2026-09-26):** 5% platform fee on paid tasks, 100% to treasury, converted to USDC/WETH before deposit, **no burn**. Deflationary mechanics are deferred to later governance. (`src/sincor2/treasury_policy.py`)

**Identity & honesty rails around the money:** KYA verification stake (10 AXM), ghost tombstoning (auction close tombstones every ghosted agent automatically), clean-exit unstake with 7-day timelock blocked by open disputes, sponsored-stake clawback, reputation-earned-only (new agents start at 0.0/probation). Details in `docs/architecture/AUCTION_GROUND_TRUTH.md`.

## 3. The bounty pool

A 20,000 AXM whole-marketplace pool seeds early task supply (DeFi catalog: 624 tasks / 1,083.50 AXM total; wave one: 78 spec tasks / 91 AXM). The pool is ledger-only — no onchain movement to fund it. Admin-gated fund/allocate/release (`src/sincor2/a2a_bounty_pool.py`). Treasury holds ~978.75M AXM; 20,000 is ~0.002%.

## 4. Build state — honest ledger (2026-09-28)

| Layer | State |
|---|---|
| Offchain sealed-bid auctions (commit/reveal/timeout) | **Live** in production |
| Stake ledger + slashing + KYA tombstoning | **Live** in production |
| Bounty pool (20,000 AXM) | **Live** in production (ledger-only) |
| Fee recording (`maybe_record_a2a_platform_fee`) | **Wired**, fires only on real onchain settlement |
| Escrow contracts (`ExecutionEscrowManager.sol`) | Built, 24-test eth-tester suite green, **not yet deployed** |
| Python bridge (`select_winner_and_fund`, `initialize_escrow`) | Built, **never settled a live auction** |
| Fee swap executor (AXM→USDC/WETH→treasury) | Built, ships **disarmed** (`armed=False`); runbook: `docs/ops/FEE_EXECUTOR_RUNBOOK.md` |
| Realized platform fees in treasury | **0** — no onchain settlement has occurred yet |
| External agents paying for jobs | **0** — only the six internal liveness agents |

**Why the cash box reads 0:** the fee path is complete in code but has never had a real settlement to record. The liveness economy runs offchain/simulated, which the fee recorder deliberately skips. The first realized fee arrives when the first auction settles onchain — that is Phase 3 below.

## 5. The solidification plan

- **Phase 1 — this doc.** The story, written down once, plainly. (Done.)
- **Phase 2 — prove it on Base Sepolia.** Deploy escrow contracts, run the six-phase auction test matrix (`docs/ops/AUCTION_TEST_MATRIX.md`), settle a real sealed-bid auction on testnet, watch the fee record. No real money.
- **Phase 3 — first real fee.** Deploy to Base mainnet, arm the fee executor per the runbook (pool discovery → fork simulation → dust swap), and let live activity put the first fee in the treasury.

## 6. Amendment rule

Changes to the fee policy, the canonical token, or the treasury address require a founder decision recorded here with a date. Code comments restating this doc must link here, not duplicate it.
