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

## Addendum 2026-09-25 — auction-core selection bridge (implemented)

The sealed-bid core had no winner selection; the escrow module had no caller.
The bridge (`contracts/CommitRevealAuction.sol`, commit `1b14d64`):

- **Poster = opener.** `openAuction` records `msg.sender` as the poster
  (hiring party). Signature unchanged.
- **On-chain Vickrey.** `bidders[]` is tracked per auction at commit time;
  `vickreyResult()` returns (lowest revealed bidder, second-lowest price),
  first-price fallback for a sole bidder, ties to the earliest committer.
- **`selectWinnerAndFund(auctionId, creditToApply)`**, poster-only, after the
  reveal deadline: computes the Vickrey outcome and atomically calls
  `escrow.initializeEscrow`, forwarding the poster's ETH. The escrow's exact
  two-sided funding check makes mispriced funding revert atomically.
- **Timeout rule (re-ratified 2026-09-25):** the bridge originally gated
  permissionless `timeout()` behind `revealDeadline + SELECTION_WINDOW`
  (1 hour). Reverted to the ratified instant rule — `timeout()` is callable
  the moment the reveal deadline passes. Rationale for instant: the poster's
  agent submits `selectWinnerAndFund` deterministically at the deadline;
  a timeout race has no payoff (timeout pays no reward, commits carry no
  bonds), so griefing is irrational; worst case of a missed deadline is a
  wasted round, not fund loss.
- Execution durations (stake-deposit window, execution, challenge,
  adjudication) are owner-settable deployment parameters on the auction core;
  `minStakeBps` is immutable on the escrow (constructor). Concrete
  `minStakeBps` / challenger-bond values await the calibration re-run.
- Status: compiles (solc 0.8.24, via-IR; 3,509 bytes). Integration tests and
  the calibration study are in flight.

## Addendum 2026-09-25 — review findings and calibration (merge-readiness)

Adversarial review of the branch (2026-09-25) returned NOT-READY on two P0s,
both fixed: (1) `pragma solidity 0.8.24` exact-pin broke `forge build`
(foundry.toml pins solc 0.8.27) — relaxed to `^0.8.24` on all three
contracts; (2) `marketplace/contract_net/keccak.py`'s eth-hash backend
(`pycryptodome`) was missing from requirements.txt — fresh installs would
ImportError the entire money path. Also fixed: `minStakeBps` constructor
bound (1..10000 bps), `ChallengerBondUpdated` event, zero-commit rejection.

- **Stake calibration re-run (TOA, 2026-09-25):** recommend
  `minStakeBps = 5000` (50% of bid) and `challengerBond = 0.02 ETH`.
  Residual garbage margin +2.2% of bid at 5000 bps (fully closed ~5437 bps
  at mean adjudicator accuracy); honest participation stays strongly
  positive. `minStakeBps` is immutable — changing it needs a fresh
  deployment, so the value must be decided before mainnet. Full tradeoff
  curve in the study notes.
- **Poster fast-path: explicitly deferred.** The ratified record allows the
  poster to adjudicate directly when acceptance is machine-checkable, but
  the contract cannot observe "machine-checkable" onchain, and letting the
  poster slash unilaterally reintroduces the bias the optimistic layer
  exists to prevent. Deferred until a checkable predicate (e.g. a test-
  harness attestation format) is designed. Poster disputes remain free to
  file; slashing stays adjudicator-only.
- **Adjudicator trust assumption (documented):** the adjudicator is a single
  key with unilateral 50%-slash power; nothing onchain yet involves the
  optimistic batch layer. Poster+adjudicator collusion is profitable in
  principle. Mitigation path: multisig/timelock adjudicator or onchain
  batch-digest verification — tracked as follow-up work, required before
  mainnet value flows.
- **Lazy-poster stress result:** if the poster files disputes w.p. 0.30
  instead of 0.85, no feasible stake deters garbage (EV ≈ +60% of bid).
  The challenge channel (poster vigilance + optimistic batch watchers) is
  load-bearing, not optional — reinforces the adjudication follow-ups above.

## Addendum 2026-09-25 — payout liveness fix (pull-payment fallback)

The 24-test eth-tester suite (tests/pytest/test_execution_escrow.py) exposed a
genuine liveness bug: all ETH payouts used push-style `_safeTransfer`, so a
recipient whose receive() reverted permanently bricked escrow resolution --
ghosting timeout, upheld-dispute resolution, and optimistic payout could never
complete, locking poster ETH, worker stake, and challenger bonds with no
recovery path.

Fix (implemented in ExecutionEscrowManager):
- `_safeTransfer` replaced by `_payout`: tries the push via low-level call;
  on failure the funds are queued in `pendingWithdrawals[recipient]` (event
  `PaymentQueued`) instead of reverting the transaction.
- New `withdraw()`: lets any recipient pull their own queued funds; a still-
  reverting recipient keeps its funds queued but never blocks anyone else.
- The re-auction credit ledger remains non-withdrawable: `withdraw()` only
  moves queued ETH, and no function converts credits into ETH.

Also tightened: `depositStake` now requires the EXACT proportional stake
(`msg.value == required`, reverts on overpayment). Previously overpayment was
silently absorbed into `agentStake`, inflating the slash basis beyond the
calibrated `minStakeBps` fraction.

24/24 escrow tests pass, including reverting-poster ghosting/dispute cases,
griefing-worker optimistic timeout, and an end-to-end withdraw() pull flow.

## Addendum 2026-09-25 — parameter ratification (user)

- `minStakeBps = 5000` (50% of bid value) and `challengerBond = 0.02 ETH`
  ratified as deployment parameters, per the TOA calibration study
  (stake_calibration.md). Modeled: strategic-garbage profit +17.1% -> +2.2%
  of bid; honest expected loss 4.7% -> 6.3%; frivolous disputes ~0.7%.
  Caveats stand: modeled, not historical; poster vigilance is load-bearing
  (at 30% dispute filing no stake level deters garbage); external
  challengers have no bounty, so rational external enforcement is weak.
- Poster fast-path (poster-triggered slash for machine-checkable
  acceptance) remains DEFERRED: no onchain predicate for "machine-checkable"
  exists yet, and unilateral poster slashing would reintroduce poster bias.
  Slashing stays adjudicator-only until then.
- Selection timeout re-ratified as INSTANT (see bridge addendum above).

## Addendum 2026-09-25 — selection-bridge adversarial review

Dedicated review + 24-test eth-tester suite
(tests/pytest/test_selection_bridge.py) for the auction-core selection
bridge (selectWinnerAndFund / vickreyResult / instant timeout).

Findings fixed:
- Price-domain truncation (Medium): reveal() accepted any uint256 price,
  but selectWinnerAndFund downcasts to the escrow's uint96 bidAmount, and
  Solidity explicit downcasts truncate silently. A bidder could reveal an
  unrepresentable price (>= 2^96 wei) at zero cost (no commit bonds) and,
  landing as winner or second-lowest, brick selection for the auction
  (poster forced to timeout()). No fund theft — the escrow's exact-funding
  check still holds. Fix: reveal() now reverts PriceTooLarge above
  type(uint96).max (~79B ETH, no legitimate bid exceeds it).

Reviewed and accepted as-is (no change):
- Unbounded bidders[] iteration: selection is O(n) over committers, but
  each commit costs the griefer ~80k gas while timeout() stays O(1) and no
  poster funds are ever at risk pre-selection. Economically irrational to
  exploit; a cap would create a worse slot-filling grief vector.
- Zero-price reveals: representable, but the escrow rejects bidAmount=0,
  so selection reverts atomically and the poster falls back to timeout().
- Timeout/select race: instant permissionless timeout can frontrun the
  poster's selection; the poster loses gas only (ratified behavior).
- Zero/degenerate durations: opener-is-poster makes weird windows
  self-harm only; owner-set execution params are trusted config.
