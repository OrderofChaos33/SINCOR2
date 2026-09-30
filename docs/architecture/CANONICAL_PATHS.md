# Canonical Paths — SINCOR2

**Status:** living reference, 2026-09-30 (w54; supersedes the v1 draft that shipped
on the in-flight `xioix/buildout-23-p1-cleanup` branch, which contained one
fictional line — a claimed `fee_event_listener.py` that does not exist; corrected
in §2 below). When any module docstring, runbook, or page disagrees with this
doc about which path is canonical, **this doc wins** until amended here.

Resolves the two Phase-0 repository findings:

- **G5.12** — three auction paths, with the inbound blueprints diverging from the
  money path.
- **G5.13** — eight treasury/payment modules with overlapping responsibilities
  and no documented canonical.

Rule: there is exactly one canonical path per money movement. Everything else is
labeled **GRANDFATHERED** (deliberate compat, kept working), **INTERNAL**
(test/tooling only), **DORMANT** (no callers), or **QUARANTINED** (do not use;
deletion needs founder review).

Companion fixes: docstring/call-graph alignment for `contract_net.py`,
`marketplace/contract_net/engine.py`, and `order_fulfillment.py` (G5.9–G5.11)
lives on the in-flight `xioix/p1-mislabeled-files` branch — this doc assumes
those land and does not duplicate them.

---

## 1. Auction paths (G5.12)

| Path | Module(s) | Status | Notes |
|---|---|---|---|
| Sealed-bid Vickrey (commit/reveal) | `marketplace/contract_net/` via `src/sincor2/blueprints/contract_net.py` (`/api/contract-net`), bootstrapped in `src/sincor2/platform_bootstrap.py` | **CANONICAL — the money path** | Winner selection, escrow funding, slashing. Spec: `docs/architecture/AUCTION_GROUND_TRUTH.md`. Onchain mirror: `ExecutionEscrowManager` (`onchain/src/`). |
| Plaintext scoring | `calculate_bid_score()` / `stage_payout()` from `src/sincor2/contract_net.py`, used by `src/sincor2/a2a_inbound_market.py` | **GRANDFATHERED** | Non-sealed tasks only — sealed tasks reject plaintext bids outright (`a2a_inbound_market.py` `create_task` docstring). Kept for pre-shim clients on operator-created tasks. Not the production auction path. |
| Sync evaluator | `ContractNetEvaluator` in `src/sincor2/contract_net.py` | **INTERNAL ONLY** | TOA self-improvement loop (`src/sincor2/wardrobe/self_improve.py`) + tests. Reads bids with no signature verification; fail-closed on external stores (raises unless `allow_external_store=True`); safe only with the in-process `MemoryHashStore`. |
| Multi-criteria first-price | `sincor2.bidding_engine.BiddingEngine` | **DORMANT** | Zero production callers (verified by repo-wide grep 2026-09-30; referenced only by docstrings, `marketplace/README.md`, and its own module). Do not build on it; deletion needs founder review. |

Chain helpers that happen to live in `src/sincor2/contract_net.py`
(`BASE_CHAIN_ID`, `probe_base_chain`, `ESCROW_ADDRESS`) are **constants and
helpers, not auction logic** — the inbound blueprints importing them
(`a2a_inbound.py`, `a2a_inbound_ext.py`) is fine and does not make those
blueprints "the auction path".

**Divergent callers — explicitly grandfathered (recorded decision, not migrated):**

- `src/sincor2/a2a_inbound_market.py:30` imports `calculate_bid_score` /
  `stage_payout` (used at :555, :712, :763). **Decision:** keep. Migration to
  the sealed-bid path would break pre-shim plaintext-bid clients; the sealed
  path is the forward direction and sealed tasks already reject plaintext bids.
  Removing this is a founder-visible breaking change, not cleanup.
- `src/sincor2/a2a_inbound.py:20` and `src/sincor2/a2a_inbound_ext.py:11`
  import `calculate_bid_score` / `stage_payout` / `BASE_CHAIN_ID` /
  `probe_base_chain` — the chain helpers are unaffected; scoring imports are
  unused-by-routes or test-adjacent and harmless.

---

## 2. Treasury / payment modules (G5.13)

Verified caller counts below are from a repo-wide grep on 2026-09-30
(excluding self-imports and tests unless noted).

| Module | Responsibility | Status | Callers |
|---|---|---|---|
| `src/sincor2/payment_verifier.py` | Onchain **AXM** payment verification (`PaymentVerifier`): multi-RPC (Alchemy/QuickNode/public Base), exponential backoff, 24h SQLite cache. Read-only. | **CANONICAL verifier** | `a2a_bootstrap.py` (installed for A2A) + tests |
| `src/sincor2/treasury_inflow.py` | **Measurement ledger** for the CEO KPI (treasury inflow): append-only JSONL events, 24h rolling totals, optional live Base RPC snapshot of the canonical treasury wallet. **Never moves funds.** | **CANONICAL measurement** | `treasury_settlement.py`, `x402_payments.py`, `agents/treasury_execution_agent.py`, `app.py`, `blueprints/monitoring.py` |
| `src/sincor2/treasury_settlement.py` | Fee-only realized-inflow recording for the A2A settlement path; feeds `treasury_inflow.record_inflow`. **Never moves funds.** | Measurement helper | `a2a_integration.py`, `platform_payments.py` + tests |
| `src/sincor2/platform_payments.py` | Human billing/checkout surface (AXM primary; SINC legacy renewals only). Read-only `verify_treasury_transfer()` (eth_getTransactionReceipt + ERC-20 Transfer log parsing), live at `POST /api/platform/verify`. | Billing surface | `agent_billing.py`, `mvp_app.py`, `mvp_blueprints/billing.py`, `subscription_scheduler.py`, `x402_payments.py` |
| `src/sincor2/treasury_hold.py` | Policy constants (`FEE_DESTINATION_PCT=100`, `PLATFORM_FEE_BPS=500`; HOLD lifted 2026-09-10). `standing_order()` consumed by the KYA quest surface. | Constants | `kya/blueprint.py` + tests |
| `src/sincor2/treasury_policy.py` | Conversion-policy config + `convert_before_treasury_if_needed()`. **WARNING — signal only:** `converted=True` means "should convert"; **no swap is executed**. The real executor (`onchain/fee_conversion_executor.py`) ships disarmed (`armed=False`) with zero production callers. Do not treat a `True` return as settled conversion. | Policy signal (see §3) | `sadas_orchestrator.py` (only production caller) |
| `src/sincor2/x402_payments.py` | HTTP 402 micropayments in SINC (spec §5.4). | Micropayment surface | `mvp_app.py`, `mvp_blueprints/billing.py` + tests |
| `src/sincor2/sinc_payment_verifier.py` | Onchain SINC transfer verification (`SINCPaymentVerifier`, 8-decimal SINC scaling). | **QUARANTINED — zero importers. Do not import.** Retained (not deleted) because it is the only SINC-verification implementation; deletion needs founder review in case SINC billing ever returns. | none |

Related (outside the 8, for completeness):

- `src/sincor2/onchain/fee_conversion_executor.py` builds Uniswap V4
  Universal-Router + Permit2 swap calldata and pre-broadcast eth_call, but
  ships **disarmed** (`armed=False`) with zero production callers. Arming is
  backlog item 32 (founder ceremony: pool pin + fork-sim + forwarder key in
  Secure Vault).
- **There is no onchain fee-event listener.** Nothing feeds
  `record_pending_conversion()` from production today; a recorded fee inflow
  does not become a conversion obligation automatically (backlog item 33).

---

## 3. Known dangerous shapes (documented, not fixed here)

- `treasury_policy.convert_before_treasury_if_needed()` returning
  `converted=True` while performing no swap (fixed by backlog item 34, which
  flips it to `converted=False` until an executor is armed).
- `marketplace/settlement.py` docstring says the 5% fee "is routed to the
  treasury"; it is recorded in a process-local dict (`route_to_treasury()`
  marks conversion `'status': 'pending'`). Fixed by backlog item 35.
- x402 "SINC micropayment" labels vs the no-new-SINC-billing rule (item 35).

---

## 4. How to keep this true

- New money-movement code must name its canonical section above in its module
  docstring, or amend this doc first.
- A second canonical path for the same movement is a bug: file it, don't ship it.
- Grandfathered paths stay working and stay labeled; removing one is a
  founder-visible breaking change, not cleanup.
