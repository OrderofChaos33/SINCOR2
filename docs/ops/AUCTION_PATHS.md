# Auction Paths — Canonical Declaration

Operator reference for SINCOR2's task-market auction code. Last verified 2026-09-25.

## 1. Canonical paths

- **MONEY path — `marketplace/contract_net/`**: sealed-bid Vickrey for priced
  agent work. Phases: filter (cosine skill match, invite top 3–5, ~12% junior
  reservation) → invite → sealed (one EIP-712 envelope per agent, 120s TTL) →
  cleared/failed. Lowest valid price wins; winner is **paid the second-lowest
  price**. Prices are integer micro-AXM. Entry: `ContractNetEngine.run()`.
- **QUALITY path — `src/sincor2/bidding_engine.py`**: `BiddingEngine`
  multi-criteria scoring for non-priced allocation — 40% confidence, 25%
  reputation, 20% cost efficiency, 10% plan quality, 5% archetype fit. Every bid
  is evaluated in isolation so one crashing bidder cannot kill the round.
- **LEGACY — `src/sincor2/contract_net.py`**: `ContractNetEvaluator`
  (score = 0.3·reputation − 0.4·bid^1.1 − 0.3·minutes). Internal use only —
  currently the TOA self-improvement loop (`src/sincor2/wardrobe/self_improve.py`).
  **Do not route production markets through it**: bids are unauthenticated
  Redis hashes.

## 2. Trust boundaries

- **Redis**: `task:{id}:meta`, `task:{id}:bids`, `agent:{id}:stats`, pub/sub
  `tasks:broadcast` are unauthenticated. Must be internal-only, with AUTH and
  TLS. Anyone with write access can forge bids or inflate reputation.
- **Escrow signer key** (`ESCROW_SIGNER_KEY` / `PAYOUT_PRIVATE_KEY` /
  `BASE_SIGNER_KEY`): moves real AXM. Highest-value secret in the system.
  Needs a rotation policy.
- **Reputation store**: 25–30% of every bid score. Writers must be
  tamper-resistant — if an agent can influence its own stats, the auction is
  gameable regardless of mechanism.
- **Commit-reveal contract** (`contracts/CommitRevealAuction.sol`): hides bids
  from MEV during the commit window, but has **no onchain deadlines**. Timing
  is enforced offchain; a stalled coordinator leaves commits hanging.

## 3. Invariants operators must preserve

1. **One award write per auction.** `BiddingEngine.run_auction_from_market()`
   must write its own result — never follow it with a second award pass
   (`task_market.evaluate_and_award_task`) that can crown a different winner.
2. **Token budget controller is required in production.**
   `BiddingEngine(token_controller=None)` silently disables the daily token
   ceiling; the module singleton locks in the first caller's controller.
3. **Staged payouts are not onchain transactions.** `stage_payout()` returns
   `mode: "staged"` with a fabricated `staged_tx_digest` until a live signer
   broadcasts. Never display a staged digest as a transaction hash.
4. **`SINCOR_ALLOW_SHADE` must never be set in production.** The `shade`
   parameter on `ContractNetEngine.run()` scales agents' true prices and is a
   test-only hook; the engine honors it only when `SINCOR_ALLOW_SHADE` is
   exactly `"1"`, otherwise it is ignored with a warning. Setting that
   variable in production re-enables price-rigging of live clearing.

## 4. Known follow-ups (not fixed in this PR)

- Onchain commit/reveal deadlines and timeout/refund path in
  `CommitRevealAuction.sol`.
- Migrate hand-rolled EIP-712/keccak (`marketplace/contract_net/eip712.py`,
  `keccak.py`) to an audited library; verify nonce/deadline enforcement.
- Winner slashing / stake-to-bid: currently no economic downside for a winner
  that takes a task and fails.
- Monitoring alerts: no-bid rate, bid failure rate, staged-but-unbroadcast
  payouts.
- KMS/HSM for the escrow signer key.
