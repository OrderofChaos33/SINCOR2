# Auction Security Decisions — Ratified 2026-09-25

Design decisions for the three open auction-hardening topics, each resolved
by a TOA-style study (simulate → Nadaraya-Watson manifold sweep → forecast →
collapse) using the repo's actual forecaster (`agents/toa/forecaster.py`).
Status: **ratified by operator 2026-09-25**. These are build directives, not
aspirations.

## 1. Commit/reveal windows — Solidity deadlines

- **Windows: 5-minute commit + 5-minute reveal**, as per-auction constructor
  parameters (not hardcoded). Agents act in seconds; five minutes covers
  RPC/reorg/nonce headroom without slowing the A2A market.
- **Timeout regime: permissionless `timeout()` after the reveal deadline.**
  Unrevealed commits are ignored (not bonded, not punished); the timeout call
  finalizes the auction so a stalled coordinator cannot leave commits hanging.
- Study rejected: non-reveal bonds (positive bonds reduced participation and
  taxed honest network failures without improving clearing — optimal bond was
  0 AXM) and auctioneer-only closure (liveness risk).
- Assumes reveal transactions cannot be selectively censored.
- Touches: `contracts/CommitRevealAuction.sol` (currently commit/reveal
  storage only — no onchain windows, winner selection, or settlement).

## 2. EIP-712 migration

- **The hand-rolled keccak + EIP-712 digest is byte-identical to `eth_account`**
  (200/200 random keccak vectors; 500/500 full bid digests, verified
  2026-09-25). No digest-format change, no cutoff, no dual-verification.
- **Migrate big-bang (shadow period T\*=0)** to `eth_account`, add a permanent
  differential-vector CI test, delete the custom crypto.
- **Secp256k1 required on the money path.** HMAC-SHA256 stays accepted only as
  a demo/test fallback — symmetric "signatures" must never clear priced work.
- Touches: `marketplace/contract_net/eip712.py`, `keccak.py`.

## 3. Winner slashing

- **Trigger: hybrid.** Full-stake slash on ghosting (no submission by
  deadline — objective, fast, onchain-verifiable) + half-stake slash on
  quality miss. Ghosting-only slashing was modeled and *dominated*: its
  optimal stake was 0 AXM, because slashing ghosting without touching quality
  reroutes strategic failure into garbage submission, which is worse (junk
  gets paid, no re-auction).
- **Stake sized as a fraction of bid value** (meaningful, not fixed — the
  manifold optimum sat at the top of the tested range for every viable
  trigger).
- **Destination: 100% to a poster re-auction fund.** The NW manifold over the
  poster share was monotonic to f\*=1.0. Compensating the poster is the
  single biggest lever (demand-side: p_post 0.97 vs 0.87 for burn/treasury).
  Burn vs treasury scored identically in-model — that choice is narrative,
  not mechanism.
- **Adjudication: optimistic batch layer** (`marketplace/optimistic/batch.py`
  already carries slash/freeze logic) — keeps judge and beneficiary separate.
  **Fast path:** the poster may adjudicate directly when acceptance criteria
  are machine-checkable (tests pass, schema valid); no discretion, no bias.
- Touches: new staking/slashing module; optimistic batch dispute wiring;
  poster-fund accounting.

## What the studies assumed (read before building)

- All findings are **modeled, not historical**: strategic agents maximize
  payoff across ghost/garbage/honest-work; honest agents crash 2% and miss
  quality 5%; quality-check catch rate 85%; adjudicator accuracy 90–95%.
- The garbage-substitution result (killing ghosting-only slashing) is
  mechanism-design reasoning — robust qualitatively, magnitude depends on
  payoff parameterization.
- Forecast-based rankings within the top design cluster (b/c/d triggers;
  C/D/E destinations) are close; the reported order is indicative, the
  *dominance* relations (quality must be slashed; poster must be compensated)
  are the reliable signal.
- Research scripts were throwaway (`/tmp/*_study.py`, temp venv) and are not
  committed. Re-run before finalizing parameters.
