# Canonical Paths — SINCOR2

**Status:** living reference, 2026-09-30. When any module docstring, runbook, or
page disagrees with this doc about which path is canonical, this doc wins until
amended here.

This resolves the two Phase-0 repository findings:

- **G5.12** — three auction paths with the inbound blueprints diverging from the
  money path.
- **G5.13** — eight overlapping treasury/payment modules with no documented
  canonical.

Rule: there is exactly one canonical path per money movement. Everything else is
labeled below as **grandfathered** (deliberate compat, kept working), **internal**
(test/tooling only), **dormant** (no callers), or **quarantined** (do not use;
deletion needs founder review).

---

## 1. Auction paths (G5.12)

| Path | Module(s) | Status | Notes |
|---|---|---|---|
| Sealed-bid Vickrey (commit/reveal) | `marketplace/contract_net/` via `src/sincor2/blueprints/contract_net.py` | **CANONICAL — the money path** | Winner selection, escrow funding, slashing. Spec: `AUCTION_GROUND_TRUTH.md`. Onchain mirror: `ExecutionEscrowManager` (`onchain/src/`). |
| Plaintext scoring | `calculate_bid_score()` / `stage_payout()` from `src/sincor2/contract_net.py`, used by `src/sincor2/a2a_inbound_market.py` | **GRANDFATHERED** | Non-sealed tasks only — sealed tasks reject plaintext bids outright. Kept for pre-shim clients on operator-created tasks. Not the production auction path. |
| Sync evaluator | `ContractNetEvaluator` in `src/sincor2/contract_net.py` | **INTERNAL ONLY** | TOA self-improvement loop (`wardrobe/self_improve.py`) + tests. Reads bids with no signature verification; safe only with in-process `MemoryHashStore`. |
| Multi-criteria first-price | `bidding_engine.BiddingEngine` | **DORMANT** | No production callers (referenced by tests and the legacy docstring). Its additive Vickrey helper delegates to `marketplace.contract_net`. |

Chain helpers that happen to live in `src/sincor2/contract_net.py`
(`BASE_CHAIN_ID`, `probe_base_chain`, `ESCROW_ADDRESS`) are **constants and
helpers, not auction logic** — importing them from the inbound blueprints is
fine and does not make those blueprints "the auction path".

**Divergent callers, explicitly grandfathered** (not migrated):

- `src/sincor2/a2a_inbound_market.py:30` imports `calculate_bid_score` /
  `stage_payout` — the grandfathered plaintext path above. Migration would break
  pre-shim clients; the sealed-bid path is the forward direction.
- `src/sincor2/a2a_inbound.py` and `src/sincor2/a2a_inbound_ext.py` import
  `BASE_CHAIN_ID` / `probe_base_chain` — chain helpers, unaffected.

---

## 2. Treasury / payment modules (G5.13)

| Module | Role | Status |
|---|---|---|
| `src/sincor2/payment_verifier.py` | Onchain **AXM** payment verification (`PaymentVerifier`); read-only RPC, installed via `a2a_bootstrap` | **CANONICAL verifier** |
| `src/sincor2/treasury_policy.py` | Conversion policy: AXM/SINC → USDC/WETH before treasury deposit; fail-closed signal | **CANONICAL policy** |
| `src/sincor2/treasury_inflow.py` | Measurement ledger: append-only JSONL inflow events, 24h rolling totals | **CANONICAL measurement** — never moves funds |
| `src/sincor2/treasury_settlement.py` | Fee-only realized-inflow recording for the A2A settlement path; feeds `treasury_inflow` | Measurement helper — never moves funds |
| `src/sincor2/treasury_hold.py` | Policy numbers (fee destination, `PLATFORM_FEE_BPS = 500`); HOLD lifted 2026-09-10 | Constants |
| `src/sincor2/platform_payments.py` | Human checkout/billing (AXM primary; SINC legacy renewals); read-only `verify_treasury_transfer` | Billing surface |
| `src/sincor2/x402_payments.py` | HTTP 402 micropayments in SINC | Micropayment surface |
| `src/sincor2/sinc_payment_verifier.py` | Onchain SINC transfer verification (`SINCPaymentVerifier`) | **QUARANTINED** — zero importers. Do not import. Deletion needs founder review: removing it would lose the only SINC-verification implementation if SINC billing ever returns. |

Related (not in the 8, for completeness): the fee-conversion executor
(`src/sincor2/onchain/fee_conversion_executor.py`) builds Uniswap V4 swap
calldata but ships **disarmed** (`armed = False`) with zero production callers;
the onchain fee-event listener (`src/sincor2/onchain/fee_event_listener.py`)
records fee inflows as ledger obligations only. Neither moves funds today.

---

## 3. How to keep this true

- New money-movement code must name its canonical section above in its module
  docstring, or amend this doc first.
- A second canonical path for the same movement is a bug: file it, don't ship it.
- Grandfathered paths stay working and stay labeled; removing one is a
  founder-visible breaking change, not cleanup.
