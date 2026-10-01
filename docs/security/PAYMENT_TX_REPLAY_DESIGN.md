# Payment-Transaction Replay Prevention — Design Options

**Status:** design only — no code changed, no behavior changed. Founder decision required before implementation.
**Date:** 2026-09-30 · **Wave 37** · base `fd96801`
**Problem (gap audit, P3):** a confirmed payment transaction can be reused. Nothing today prevents the
same `tx_hash` from being presented as proof of payment twice, or a transaction paid by wallet A from
being claimed by caller B.

## 1. Current state (verified against the code)

**What already works:**
- `src/sincor2/payment_verifier.py` is the canonical AXM verifier (designated canonical by wave 23;
  note an older inline `PaymentVerifier` still exists in `a2a_integration.py:1600` — the merge pass
  should retire it in favor of the module). `is_verified(tx_hash, expected_amount_wei, expected_to)`
  reads the receipt + ERC-20 `Transfer` logs over RPC.
- Wave 13 added `PaymentVerifier.verified_amount_wei(tx_hash)` (actual AXM wei transferred) and
  `_reconcile_axm_paid(tx_hash, claimed_wei, rpc_id)` in `a2a_integration.py` — the caller-claimed
  amount is now reconciled against the chain before recording (gap G2.5 closed).
- `src/sincor2/persistent_store.py` exists on base with `get_store().kv_get/kv_set`; the verifier
  already uses it for its verification-result cache. Wave 15's `a2a_shared_state.py` (separate branch)
  adds atomic claim primitives — see the merge-time adoption guide
  `docs/architecture/SHARED_STATE_ADOPTION.md` (wave 30).

**Verified wallet identity exists (parallel branches, read-only reference):**
- Wave 18: `_resolve_quota_identity()` (`a2a_integration.py:1593` on `xioix/buildout-18-caller-quotas`) —
  EIP-191 signature over `SINCOR-QUOTA|<skill_id>|<input_hash>|<timestamp_ms>`, mandatory wallet
  claim checked against the recovered signer.
- Wave 24: `_resolve_verified_wallet()` (`a2a_integration.py:1551` on
  `xioix/buildout-24-reputation-identity`) — byte-identical canonical messages, so one signature
  serves quota + reputation.
- Wave 32: `src/sincor2/a2a_identity.py` (on `xioix/buildout-32-first-registration`) — standalone
  identity module; recommends `SINCOR_REGISTRATION_PROOF_REQUIRED=1` as production posture.
- All three are near-duplicates by design (parallel branches); the merge pass dedupes them into one
  shared helper. Any sender-binding design should use the **deduped** helper, not a fourth copy.

**What is NOT prevented (the two attacks):**
- **Attack 1 — replay:** the same `tx_hash` pays for task 1, then is re-presented for task 2.
  `is_verified` is a pure predicate; `_record_a2a_settlement()` (`a2a_integration.py:2402`) records
  without checking prior use. Cost to attacker: zero (one payment, N credits).
- **Attack 2 — cross-caller claim:** caller B presents the `tx_hash` of a payment made by wallet A
  (someone else's task payment, a public treasury transfer, etc.) as their own. Amount and
  destination are verified; the **sender is never bound to the caller**.
- Related residual (G2.18/G2.4): the `settle()` route (`a2a_integration.py:1960`) echoes a
  caller-supplied `tx_hash` into the proof-of-settlement without verifying it belongs to the task.
  Any replay design must cover `settle()` as well as `_handle_send`.
- `0xSIMULATED…` hashes (`a2a_integration.py:1512,1574`) are accepted on test/simulated paths and
  must **never** enter a spent-tx ledger as if they were real chain payments.

## 2. Design 1 — Persistent spent-transaction ledger

**Mechanism.** On first acceptance of a payment, atomically claim
`paytx:spent:<chain_id>:<tx_hash_lower>` in the durable store. Value is JSON:
`{task_id, caller_key, amount_wei, first_seen_ts}`. A second presentation of the same hash for a
*different* task → `409 Conflict` ("payment already consumed by task X"). Same task re-presented
(e.g. client retry) → idempotent success returning the original record.

**Hook points (exact):**
- `_handle_send` in `a2a_integration.py` — at the two `_reconcile_axm_paid(...)` return sites (wave 13):
  claim the hash only *after* amount reconciliation succeeds.
- `settle()` route (`a2a_integration.py:1960`) — verify the tx first (closes G2.18), then claim before
  issuing the proof-of-settlement.
- `_record_a2a_settlement()` (`a2a_integration.py:2402`) — defense-in-depth re-check.

**Atomicity (the load-bearing detail).** Under multi-worker gunicorn, check-then-set races allow
double-accept. The claim must be a single atomic "insert if absent" — wave 15's `SharedState`
claim primitive (or the wave-30 `SharedRateLimitStore` adapter pattern) provides it; the base
`persistent_store.kv_set` does not guarantee it. Do not implement this on the in-memory dict.

**Failure modes:**
- Store outage: fail-closed (reject paid calls) is the secure posture but halts paid traffic;
  fail-open re-opens replay. This needs an explicit policy choice (recommend: fail-closed with a
  loud 503 + alert, matching the wave-15 migration's fail-closed-on-unreachable-Redis rule).
- Unbounded growth: prune entries older than 2× the dispute/adjudication window (exact window TBD
  with the disputes design); pruning is a background job, never inline.
- Reorgs: a hash claimed then reorged out is a rare edge — the receipt check is at claim time;
  document, don't over-engineer.

**Closes:** Attack 1 (replay) completely. **Leaves open:** Attack 2 — the *first* presenter of a
fresh hash wins, and nothing stops that first presenter from being a thief. (Also: a thief who
observes someone else's payment *before* the victim presents it can front-run the ledger claim —
the ledger alone cannot tell who the rightful payer is.)

**Cost/complexity:** low–medium. One atomic-claim primitive, one prune job, three hook sites, tests
for the race (multi-thread claim contention → exactly one winner).

## 3. Design 2 — Sender-wallet-to-caller binding

**Mechanism.** Extend the verifier to return the payment's actual sender: the ERC-20 `Transfer`
log's `topics[1]` (`from` address). New `PaymentVerifier.verified_payment(tx_hash)` returns
`(amount_wei, sender_address, to_address)`. Accept the payment only if `sender_address` equals the
caller's verified wallet (from the deduped EIP-191 identity helper). Callers without a verified
wallet are rejected (fail-closed) or restricted to free-tier only — the posture wave 32 already
recommends (`SINCOR_REGISTRATION_PROOF_REQUIRED=1`).

**Hook points (exact):**
- `src/sincor2/payment_verifier.py` — add `verified_payment()`; keep `is_verified()` as a wrapper.
- `_reconcile_axm_paid()` in `a2a_integration.py` — add the sender check after the amount check.
- `settle()` route — same check before issuing proof (closes the G2.18 echo).

**Failure modes (the load-bearing details):**
- **Relayers / smart-contract wallets:** `Transfer.from` is the *token sender*, which for
  Permit2/Universal-Router flows or the platform's own forwarder pattern is a **contract**, not the
  user's EOA. Strict equality breaks every legitimate relayer payment. Mitigation: an explicit
  allowlist of known relayer/forwarder contract addresses (founder-pinned, like the oracle-address
  pinning decision), or an "authorized sender" indirection where the verified wallet pre-registers
  its relayer. Either way this is an allowlist the founder must maintain.
- **Privacy/cost:** forces wallet disclosure + a signature on every paid call (already the direction
  of waves 18/24/32, but it becomes mandatory, not optional).
- **Unsigned legacy callers:** break unless grandfathered with a sunset date.

**Closes:** Attack 2 (cross-caller claim) completely for verified callers; combined with binding the
hash to the *task* at send time, it also closes replay *within* a caller. **Leaves open:**
Attack 1 across tasks — the same wallet can still present the same hash for a second task, because
the sender check passes both times.

**Cost/complexity:** medium. Verifier extension, identity requirement, relayer allowlist management,
grandfathering policy, tests with real `Transfer` logs (sender in `topics[1]`).

## 4. Design 3 — Combined

**Mechanism.** Both checks in one ordered path at each hook site:
1. Verify the tx on chain (receipt + amount + destination).
2. **Check sender == verified caller wallet** (Design 2).
3. **Atomically claim the spent hash** (Design 1).
4. Record settlement.

**Ordering matters:** the sender check comes *before* the ledger claim. If the order is reversed,
a thief can grief a victim by claiming the victim's fresh payment hash into the spent ledger first,
causing the victim's legitimate presentation to 409.

**Closes:** both attacks (replay and cross-caller claim), including the front-running variant of
Attack 2 that Design 1 leaves open. **Failure modes:** the union of both designs — atomicity,
store-outage policy, pruning, and the relayer allowlist must *all* be solved. **Cost/complexity:**
high, but it is the sum of two already-understood pieces, not a new invention.

## 5. What each design does NOT cover (out of scope, recorded honestly)

- A payer who legitimately paid and then disputes the service — that's the disputes/adjudication
  path (`a2a_inbound_market.py:1062`), not replay prevention.
- Chain reorgs deeper than the receipt check — accepted as documented residual.
- `0xSIMULATED` test paths — explicitly excluded from the ledger by the `startswith("0xSIMULATED")`
  guard at every hook site.

## 6. Recommendation

**Build Design 3 (combined), in two phases:**
- **Phase A (immediate): Design 1, the spent ledger.** It closes the zero-cost replay — the
  cheapest, highest-volume attack — with the least new machinery, and its atomic-claim primitive
  is directly reusable by Phase B. Ship with fail-closed store-outage behavior and the prune job.
- **Phase B (next): Design 2, sender binding**, gated on the deduped identity helper landing
  (merge pass) and the founder pinning the relayer allowlist. This closes the cross-caller claim
  that Phase A leaves open.

Phase A alone is a strict improvement and is safe to ship without waiting for the identity/allowlist
decisions; Phase B must not ship before them.

## 7. Founder decision required

One decision, three parts — reply with A, B, or C:

- **(A) Combined, phased (recommended):** build the spent-tx ledger now (Phase A); build
  sender-wallet binding next (Phase B) once the identity helper is deduped and you pin the
  relayer/forwarder contract allowlist.
- **(B) Spent ledger only:** accept the residual that a thief can front-run someone else's fresh
  payment hash into the ledger (the first presenter wins, rightful or not).
- **(C) Sender binding only:** accept the residual that the same wallet can replay one payment
  across multiple tasks, and commit to maintaining the relayer allowlist.

If (A) or the Phase-B half of (C): also confirm the store-outage policy — **fail-closed (503,
recommended)** or fail-open — and pin the initial relayer/forwarder allowlist (or "none yet").
