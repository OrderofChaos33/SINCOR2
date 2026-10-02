# CNP-8 — Streaming Dynamic Utility Bidding (protocol spec)

Status: implemented on `xioix/cnp-08-streaming-bids` (unmerged). Offchain
protocol layer; no contract changes, no settlement-economics changes.

## 1. Problem

In sealed-bid contract-net auctions, an agent's private valuation can move
*inside* the commit window (new cost information, inventory change, risk
update). Without streaming, the agent must either commit early to a stale
valuation or wait and risk missing the window. CNP-8 lets agents stream
*commitment updates* — each superseding the last — while preserving
sealed-bid privacy end to end.

## 2. Non-goals

- Replacing `commit`/`reveal`/`close_auction`. Settlement still runs on
  final committed bids (first-price), exactly as today.
- Changing auction economics (reserve, fees, winner rule). Founder-gated,
  out of scope.
- Zero-knowledge proofs of increment size. Increment is enforced at reveal;
  see §6.

## 3. Protocol

### 3.1 Stream lifecycle

1. **Open.** Agent calls `StreamingBidManager.open_stream(auction_id,
   agent_id, commit_deadline_ts, min_increment_wei)`. Rejected if the
   deadline is not in the future, the auction already hosts the per-auction
   stream cap, or the agent already has a stream.
2. **Update.** Agent calls `submit_update(auction_id, agent_id,
   commitment)` where `commitment` is a 32-byte hash computed with the
   *existing* `sincor2.onchain.bidder_client.commitment()` scheme —
   `keccak256(auctionId ‖ chainId ‖ price ‖ salt ‖ agentIdHash)` — using a
   **fresh 32-byte salt per update**. The plaintext price and salt never
   leave the agent until reveal.
3. **Close.** `close_stream(...)` (optional; the commit deadline closes the
   window regardless).
4. **Reveal.** Post-window, the agent reveals the full chain
   `[(price_i, salt_i)]` in sequence order. `validate_reveal_chain(...)`
   recomputes every commitment and checks the increment rule.
5. **Settle.** `effective_final_bid(...)` returns the validated final
   `(price, salt, commitment)` triple, which the agent (or relayer) feeds
   into the unchanged onchain `commit → reveal → close_auction` path.

### 3.2 What the stream store holds

Per `(auction, agent)`: ordered 32-byte commitments, sequence numbers,
receipt timestamps, deadline, `min_increment_wei`. **No prices, no salts,
no deltas.** `stream_summary()` exposes only counts/flags/deadlines.

## 4. Privacy argument

Each update is `keccak256(...)` over `(auctionId, chainId, price, salt,
agentIdHash)` with a fresh random salt. Under the PRF assumption on
keccak256, commitments are computationally indistinguishable from random
32-byte strings, and — critically — their *ordering* is uncorrelated with
the underlying price ordering. A stream observer therefore learns nothing
about valuations or their direction of movement. Proven statistically in
`tests/pytest/test_streaming_bids.py`:

- Spearman correlation between price rank and `int(commitment)` rank over
  300 samples: |ρ| < 0.15 (null-level).
- An adversary shown two agents' latest commitments guesses the higher
  bidder at ~50% over 200 trials.
- A store-content scan proves no plaintext valuation bytes are persisted.

Residual, accepted leak: update *counts and timing* are visible (same as
onchain commit transactions being visible). Values, deltas, and ordering
are not.

## 5. Anti-spam rules (enforced in `submit_update`)

| Rule | Default | Rejection code |
|---|---|---|
| Stream must be open and window live | — | `unknown_stream` / `stream_closed` / `window_closed` |
| Commitment well-formed (32 bytes) | — | `malformed_commitment` |
| No commitment replay within a stream | — | `duplicate_commitment` |
| Auth hook passes (when configured) | — | `auth_failed` |
| Minimum interval between updates | 20 s | `rate_limited` |
| Updates per stream | 12 | `update_cap_reached` |
| Streams per auction | 64 | `auction_stream_cap` |

All rejections return structured results; the handler never raises on
bad input. Payloads are fixed 32 bytes; per-update work is O(1)
amortized; critical sections are short (single RLock).

## 6. Increment rule (enforced at reveal)

`min_increment_wei` (per stream, set at open) requires
`|price_i − price_{i−1}| ≥ min_increment_wei` for every consecutive pair.
It is enforced in `validate_reveal_chain`, not at submit time, because
checking it earlier would require seeing the plaintext and break the
privacy constraint.

**Longest-valid-prefix rule.** Validation walks the chain in order and
stops at the first failure (commitment mismatch, bad input, or
increment violation). The *effective bid* is the last fully-validated
update; the failing suffix is **voided**. Consequences:

- Increment grinding (dust-sized updates) cannot move settlement: the
  ground suffix is discarded and the grind burns the attacker's own
  rate-limit budget.
- A partially-corrupt reveal degrades gracefully to the last honest
  update instead of nuking the whole stream.

## 7. Binding & replay

Binding is inherited from the contract-exact commitment scheme:
`auctionId`, `chainId`, and `agentIdHash` are hashed into every update.
A commitment streamed for auction A fails validation for auction B
(`validate_reveal_chain` recomputes with the target auction's id), and a
commitment cannot move chains. Within a stream, exact commitment replays
are rejected at submit time. Salt reuse across updates is harmless (each
hash is still an independent PRF output) but fresh salts are required by
client convention (`random_salt()`).

## 8. Authentication

Production deployments MUST construct `StreamingBidManager` with an
`auth_verifier(agent_id, auction_id_hex, commitment) -> bool`. Constructing
without one under `ENVIRONMENT=production`/`FLASK_ENV=production` raises
`ValueError` (fail-closed, mirroring `BiddingEngine`). Dev/test without a
verifier logs a warning.

## 9. Integration points (unchanged code)

- `sincor2/onchain/bidder_client.py` — commitment scheme reused verbatim.
- `close_auction` money path — untouched; consumes `effective_final_bid()`
  output like any other final commitment.
- No new products, no new routes, no DB migrations in this change.

## 10. Test evidence

`tests/pytest/test_streaming_bids.py` — 24 tests covering lifecycle,
anti-spam rejections, reveal-chain validation (incl. increment voiding,
tamper, cross-auction replay), the three privacy proofs, settlement
handoff, auth, and thread-safety. Neighboring suites:
`tests/pytest/test_bidder_client.py`, `tests/pytest/test_contract_net.py`.
