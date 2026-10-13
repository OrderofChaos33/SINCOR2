# SINCOR KYA v0

Drop-in trust layer on inbound fabric. Does not replace `/v1/a2a/register`.

- Chain: Base 8453
- AXM bond/fee: `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a`
- Do not use dead AXM `0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822`
- Phase 1: lookup only. Do not gate message/send until 50 verified records.

## Two registries (do not confuse them)

1. **Off-chain identity registry** — `src/sincor2/kya_registry.py` (+ routes in
   `src/sincor2/kya_blueprint.py`). This is the live one. States:
   `listed -> bound -> staked -> verified | revoked`, plus `expired` set by
   `refresh_status()` when the attestation liveness window lapses.
2. **On-chain anchor** — `contracts/kya/KYARegistry.sol` (465 lines, Solidity
   `^0.8.24`, MIT). Exists in this repo since the W-10 hardening pass
   (2026-09-29, reworked after adversarial review). It is a pointer layer for
   off-chain attestations — it does NOT carry the off-chain states; its state
   machine is documented below.

## Prices / fee constants (READ CAREFULLY — two sources disagree)

The spec line "Min stake: 10 AXM. Verify fee: 2 AXM" matches only ONE source.
Both are recorded here verbatim; the mismatch is real and unresolved as of
2026-10-13:

- `src/sincor2/kya_registry.py` lines 21-22 (module-level live registry) AND
  `src/sincor2/kya/registry.py` line 17-18 (kya package registry):
  `MIN_STAKE_WEI = 10 * 10**18`, `VERIFY_FEE_WEI = 2 * 10**18`
  → 10 AXM minimum stake, 2 AXM verify fee.
- `src/sincor2/kya/pricing.py` `PRICE_BOOK` says something different:
  `kya_verify: {axm: 25, usdc: 5, usd_card: 9, stake_axm_min: 50}` → 25 AXM
  verify, 50 AXM minimum stake. (`kya_list` is free: axm 0.)
- Other PRICE_BOOK entries: `sla_epoch` axm 8 (300s interval),
  `sadas_month` axm 40 (10 AXM discount if verified), `quest_reward_axm` 15,
  `escrow_take_bps` 150, `chain_id` 8453, settlement "AXM first, USDC second,
  card last".

## Hooks

`hook_listed` / `hook_heartbeat` (in `src/sincor2/kya_registry.py`) are wired in
`src/sincor2/a2a_inbound_ext.py`: the inbound register path calls `hook_listed`
and the heartbeat path calls `hook_heartbeat`.

## Live API (src/sincor2/kya_blueprint.py, Blueprint `kya` at /v1/kya)

Identity registry (auth: `POST /revoke`, `POST /unstake/*`, and the stake branch
of `POST /verify` require owner-or-admin via `_guard_kya_mutation`):

- POST /v1/kya/list
- POST /v1/kya/bind
- POST /v1/kya/verify (also applies stake when `stake_tx` + `stake_wei` given)
- POST /v1/kya/heartbeat
- POST /v1/kya/sla
- POST /v1/kya/revoke            (confirmed: exists on the live registry)
- POST /v1/kya/unstake/request
- POST /v1/kya/unstake/finalize
- GET /v1/kya/health
- GET /v1/kya/directory
- GET /v1/kya/tombstones
- GET /v1/kya/agent/{agent_id}
- GET /v1/kya/lookup?wallet=
- GET /v1/kya/{kya_id}

Additive stack (`kya_stack_bp` in `src/sincor2/kya/blueprint.py`, mounted via
`mount_kya` from `src/sincor2/kya_bootstrap.py:mount_kya_stack`). Colliding
`/v1/kya/list|bind|verify|lookup|health|heartbeat` routes are intentionally
omitted so the live identity registry stays the source of truth:

- GET /v1/kya/stack (PRICE_BOOK + quest stats + SADAS scorecard)
- POST /v1/sla/subscribe, POST /v1/sla/ping, GET /v1/sla/receipts
- POST /v1/sadas/publish (admin), POST /v1/sadas/subscribe,
  GET /v1/sadas/scorecard, GET /v1/sadas/feed
- POST /v1/polyclaw/day (admin), GET /v1/polyclaw/scorecard
- GET /v1/quest, POST /v1/quest/seed (admin), GET /v1/quest/eligibility,
  POST /v1/quest/claim

## kya/ Python package (src/sincor2/kya/)

| module | purpose |
|---|---|
| `registry.py` | `KYARegistry`: off-chain quest-package registry (listed→bound→staked→verified‖revoked); `MIN_STAKE_WEI`/`VERIFY_FEE_WEI`; JsonStore-backed |
| `sla.py` | 5-minute SLA pings bound to a kya_id; receipts, not theater |
| `sadas.py` | SADAS feed — paid scorecard, gated unless subscribed |
| `receipts.py` | Polyclaw performance receipts (publish now, vault later) |
| `airdrop_quest.py` | KYA airdrop quest — merkle verification against the AXM disperse list, then KYA verify, then a ledger reward credit (no auto-transfer) |
| `merkle.py` | OpenZeppelin-compatible keccak256 sorted-pair Merkle tree; leaf = keccak256(address20), pairing = keccak256(min‖max) |
| `pricing.py` | `PRICE_BOOK` (see price section above — differs from registry constants) |
| `blueprint.py` | Additive `kya_stack_bp` routes (see list above) |
| `store.py` | JSON persistence under `SINCOR_DATA_DIR/kya` or `data/kya` |

## On-chain KYARegistry.sol — state machine

`contracts/kya/KYARegistry.sol`. All mutators are `onlyOwner`; owner rotation is
two-step (`nominateOwner` → `acceptOwnership`, fat-finger protection only — it
does not mitigate a compromised owner key).

States: **listed → verified | revoked**. There is no `bound`, `staked`, or
`expired` on-chain; those exist only in the off-chain registry.

- `list(kyaId, agentIdHash, principal, recordHash)` — create or update a live
  attestation. Reverts `RevokedPreviously()` when the kyaId was ever revoked:
  **a revoked kyaId can never be re-listed directly** (everRevoked no-revive
  guard). If the agent hash OR the principal changes on an existing record, the
  record is unverified (identity change = new attestation; graft-unverify emits
  `SiblingUnverified`); recordHash-only updates keep `verified=true`. A
  tombstoned actor may be LISTED (visible on-chain) but can never be verified
  except through the timelocked path. The `agentToKya` pointer is kept fresh
  (cleared on hash change) and the `hashToKya`/`principalToKya` reverse indexes
  are maintained (push on fresh list, swap-and-pop move on component change).
- `verify(kyaId, recordHash)` — sets `verified=true`. Reverts
  `ActorPreviouslyRevoked()` while `revokedAgent[agentIdHash]` or
  `revokedPrincipal[principal]` is tombstoned, so a fresh kyaId for the same
  actor gains nothing. Reverts `RevokedAlready()` on a revoked record.
- `revoke(kyaId, recordHash)` — requires a listed kyaId (`NotListed()` for
  unknown ids, so a stray revoke cannot burn an unused kyaId). Sets
  `revoked=true`, `verified=false`, stamps `everRevoked[kyaId]` permanently,
  and tombstones **both** identity components:
  `revokedAgent[agentIdHash]` and `revokedPrincipal[principal]`. Then the
  **SiblingUnverified sweeps**: every OTHER live attestation sharing the burned
  agent hash (via the `hashToKya` index — the full array, since the
  `agentToKya` pointer names at most one kyaId per hash) and every other live
  attestation sharing the burned wallet (via the `principalToKya` index) is
  unverified, each actual true→false flip emitting
  `SiblingUnverified(kyaId, agentIdHash, principal)`. Sweeps are
  flip-idempotent (a sibling is unverified at most once per sweep even when it
  appears on both sides). Emits `Revoked`.
- `requestReverification(kyaId)` → `finalizeReverification(...)` — the ONLY
  re-entry for tombstoned actors. `REVERIFY_DELAY = 48 hours` must elapse
  between the two steps (`ReverifyDelayNotElapsed` otherwise). Admits exactly
  two cases: (a) a currently-revoked, ever-revoked kyaId, or (b) a live
  attestation naming a tombstoned actor (fresh-kyaId re-admission). A live
  attestation of a never-tombstoned actor is inadmissible (live updates go
  through `list()`), and a revoked record must pass through this path — it is
  never a silent live-record rewrite. `finalizeReverification` rewrites the
  record (unverified, unrevoked), **clears per-component tombstones for the
  exact agent hash and principal on the new record** (an admitted wallet or
  hash is clean for future pairings; other burned identities stay burned), and
  clears the pending request. The kyaId stays marked ever-revoked: **every
  revival requires a fresh timelocked cycle**.

Events: `Listed`, `Verified`, `Revoked`, `SiblingUnverified`,
`ReverificationRequested`, `Reverified`, `OwnerNominated`,
`NominationCancelled`, `OwnershipTransferred`.

Deployment note: contract exists in-repo (W-10 hardening, adversarially
reviewed). Deployment is NOT recorded in this spec — confirm deployment status
against the ops records before any deploy action.
