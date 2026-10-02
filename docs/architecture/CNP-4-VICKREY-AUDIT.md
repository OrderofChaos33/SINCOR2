# CNP-4 Audit: Vickrey / Second-Price Sealed-Bid Task Auctions

**Date:** 2026-10-02
**Scope:** CNP-4 (audit first — no economic change)
**Verdict:** `marketplace/contract_net/vickrey.py` (`clear_vickrey`) is **NOT** on the live
settlement path. The live A2A sealed-bid money path settles at **first price** — the winner
is paid exactly their own winning bid (`winning_bid_axm`). `clear_vickrey` is reachable only
from demo/sandbox code.

## 1. Live sealed-bid settlement path (end-to-end)

Every file below is on `origin/main` at audit time (branch `xioix/cnp-04-vickrey-audit`).

1. **Commit** — `POST /v1/a2a/bids/commit` →
   `commit_bid(task_id, agent_id, commitment)` —
   `src/sincor2/a2a_inbound_market.py:608`.
   Locks 50% of the bounty via the stake ledger; stores the commitment hash in
   `fabric.commits` (`task_id\x00agent_id` key).

2. **Reveal** — `POST /v1/a2a/bids/reveal` →
   `reveal_bid(task_id, agent_id, bid_axm, nonce, time_est_sec)` —
   `src/sincor2/a2a_inbound_market.py:673`.
   Recomputes the commitment (`sealed_commitment`, keccak preimage matching
   `CommitRevealAuction`), tops up stake if `bid*50%` exceeds the bounty lock,
   then materializes a bid record with
   `score = calculate_bid_score(bid_axm, time_est_sec, reputation)` —
   line 732. Note the reveal tiebreak note at lines 740-746: ties break to the
   earliest **COMMIT** timestamp, mirroring the ratified Vickrey tiebreak.

3. **Close / assignment** — `POST /v1/a2a/tasks/{task_id}/close` (also timer-triggered) →
   `close_auction(task_id)` — `src/sincor2/a2a_inbound_market.py:380`.
   - For sealed tasks: once past `reveal_deadline`, only `revealed` bids are
     eligible; unrevealed commits become ghosts (100% slash via stake ledger).
   - Winner selection, line 423:
     ```python
     winner = sorted(cands, key=lambda b: (-float(b["score"]), int(b["received_at"]), b["bid_id"]))[0]
     ```
     Highest composite score wins; ties break to earliest commit, then `bid_id`.
   - Settlement, line 428: `task["winning_bid_axm"] = winner["bid_axm"]` —
     **the winner's own bid price**. This is first-price settlement. There is no
     second-price step anywhere in this function.
   - Stake settlement: ghosts slashed 100% → poster re-auction credit; losers
     released; winner's lock held until proof (`stake_ledger.py:29,494`).

4. **Proof / payout** — `POST /v1/a2a/proofs` →
   `submit_proof(task_id, agent_id, receipt_hash)` —
   `src/sincor2/a2a_inbound_market.py:759`.
   `amount = float(task.get("winning_bid_axm") or task.get("bounty_axm") or 0)`
   (line 777), then `stage_payout(..., amount_axm=amount, ...)` sets
   `task["payout_axm"] = amount` and `task["state"] = "settled"` on receipt.

5. **Scoring rule** — `calculate_bid_score(bid_axm, time_est_sec, reputation)` —
   `src/sincor2/contract_net.py:128`:
   ```python
   """Higher score wins. Cheaper + faster + more reputable."""
   ```
   It is a composite (price/time/reputation), not a price-min rule. The highest
   score need not belong to the lowest price — a cheap-but-slow bid loses to a
   pricier-but-fast bid.

6. **Onchain side-car (funding only)** — `_fund_onchain_escrow` —
   `src/sincor2/a2a_inbound_market.py:495`. No-op unless `AUCTION_ONCHAIN_FUND=1`.
   When enabled and onchain reveals exist, it reads
   `vickreyResult(auctionId) -> (winner, price)` from the onchain auction
   contract (`auction_bridge.py:89`, `auction_relayer.py:183`) and funds escrow
   with that price. The Python assignment and `payout_axm` stand regardless; the
   docstring states the onchain result is funding-only (`"python_only"` is the
   normal outcome while the Python API is primary).

## 2. Where `clear_vickrey` actually is

- `marketplace/contract_net/vickrey.py:clear_vickrey` — lowest valid price wins,
  winner's clearing price = second-lowest valid bid price (their own price when
  solo). Emits an `Award` with `mechanism="vickrey-second-price"`,
  `clearing_price`, `savings_vs_first_price`.
- Its **only** call site in the repository:
  `marketplace/contract_net/engine.py:318` inside `ContractNetEngine.run()`.
- `ContractNetEngine.run()` is invoked from exactly two places, both demo-only:
  1. `src/sincor2/blueprints/contract_net.py:153` — `POST /api/contract-net/auctions`
     and `:172` — `/simulate`. The module docstring reads
     `"Additive Contract-Net API. Does not replace /api/marketplace first-price routes."`
     and the engine helper is documented as `"Demo/sandbox engine"`. Awards go to
     an in-memory history; nothing writes them into fabric tasks or the payout path.
  2. `src/sincor2/bidding_engine.py:518` — `BiddingEngine.run_vickrey_auction()`,
     whose docstring says `"Does **not** replace run_auction ... so the first-price
     market loop is unaffected."` This method has **zero callers** anywhere in
     the codebase (repo-wide grep confirms).

Nothing in `a2a_inbound_market.py`, `stake_ledger.py`, `a2a_inbound_ext.py`,
`contract_net.py`'s stage_payout path, or any route handler imports or calls
`clear_vickrey`. `close_auction`'s output snapshot carries `winning_bid_axm` /
`winner_score` — none of the `Award` fields (`clearing_price`,
`savings_vs_first_price`, `mechanism`) ever appear on a task record.

## 3. Verdict

**`vickrey.py` is wiring-only / simulation-only; it is not the settlement.**
The live money path is:

```
commit (50% stake lock) -> reveal (commitment check + score)
  -> close_auction: max score wins, winning_bid_axm = winner's OWN bid
  -> stake ledger: ghosts slashed / losers released / winner locked
  -> submit_proof: payout_axm = winning_bid_axm, task settled
```

i.e. **first-price settlement** with composite-score winner selection, not
Vickrey second-price. The sealed-bid commit/reveal machinery is real (commitment
scheme, reveal windows, ghost slashing), but the clearing rule on the money path
is not `clear_vickrey`.

### Observation (not economics, flagging for awareness)

`GET /api/contract-net/health` (`blueprints/contract_net.py:61`) advertises
`"mechanism": "vickrey-second-price"` for the demo engine while the live A2A
path settles first-price. External readers could mistake the demo label for the
platform's settlement rule. Related: a comment at the top of
`a2a_inbound_market.py` (line 33) reads "Canonical money path is
marketplace.contract_net" directly above `from sincor2.contract_net import
calculate_bid_score, stage_payout` — the two modules
(`marketplace/contract_net` vs `sincor2/contract_net`) are easy to conflate,
and the comment predates the Vickrey question. Recommend copy clarifications in
both spots (out of CNP-4 scope; no code changed here).

## 4. What routing through second-price settlement would require (FOUNDER-GATED — no implementation)

Changing settlement from first-price to second-price is an **economic change**
requiring explicit founder approval (not granted). If ever approved, the work
would be, in order:

1. **Founder decision record**: which second-price rule (pure Vickrey lowest-price
   wins, or second-price *within* the composite-score ranking), how ties break,
   how the stake top-up basis changes (currently 50% of own bid), and how
   `calculate_bid_score` interacts with price selection (Vickrey assumes price
   minimization; the live score is not monotone in price).
2. **Code change site**: `close_auction` in `src/sincor2/a2a_inbound_market.py`
   (~lines 423-431) — winner selection and `winning_bid_axm` assignment are the
   single choke point. `payout_axm` in `submit_proof` (line 777) flows from
   `winning_bid_axm` automatically.
3. **Ledger impact review**: `stake_ledger` top-ups and the 50%-of-bid commit lock
   assume first-price exposure; second-price changes the bidder's realized
   exposure and must be re-audited before wiring.
4. **Tests**: new pinning tests for second-price settlement, updated first-price
   pins in `tests/pytest/test_cnp4_vickrey_audit.py`, and the existing
   `test_sealed_bid_shim.py` expectations would change.

No economic change was implemented in this wave.
