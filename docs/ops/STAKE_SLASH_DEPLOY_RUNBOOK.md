# StakeSlashManager — Deploy Ceremony Runbook

**Status: NOT DEPLOYED.** The contract (`contracts/StakeSlashManager.sol`)
is designed, compiled (solc 0.8.24), and proven on eth-tester
(`tests/pytest/test_stake_slash_bridge.py`, 12/12 green). The Python wiring
(`src/sincor2/onchain/stake_bridge.py`, `stake_ledger.py` onchain section)
is dry-run only. Nothing here moves funds until the founder explicitly
authorizes the ceremony below and the deploy wallets are funded.

## What it is

On-chain stake deposits and adjudicator-ruled slashing for the sealed-bid
task market. Agents stake AXM (ERC-20 `transferFrom` pull); slashing runs
as a **staged flow** (J1 adjudicator hardening, 2026-10-04):

```
proposeSlash  →  appeal window  →  executeSlash | appealSlash → resolveAppeal
```

- **Stage 1 — propose.** Anyone may call `proposeSlash` with an
  adjudicator-signed EIP-191 ruling. The ruling binds contract address +
  chainid + agent + poster + amount + treasury cut + nonce + expiry +
  reason + **evidenceHash** inside the signed digest. Zero evidence
  reverts (`MissingEvidence`) — there are no evidence-free rulings. The
  agent's nonce is consumed at propose time (a ruling cannot be proposed
  twice). A ruling whose expiry precedes its `executableAt` is rejected at
  propose time — it could never execute.
- **Appeal window.** `executableAt = proposedAt + APPEAL_WINDOW`
  (immutable, **default 1 day**). Before that timestamp, the slashed agent
  **and only the agent** may call `appealSlash`, posting the fixed
  `APPEAL_BOND` in collateral token (default 0.01 units; refunded however
  the appeal resolves — it prices out spam, not honest agents). While a
  proposal is appealed, `executeSlash` reverts. The appeal and execution
  windows are disjoint by construction: no block admits both, so the
  appeal cannot be front-run.
- **Stage 2a — resolveAppeal.** The `appealAuthority` (immutable,
  rotatable via `rotateAppealAuthority`) upholds (slash executes, bond
  refunded) or rejects (ruling tombstoned, no slash, bond refunded).
- **Stage 2b — executeSlash.** Permissionless once the window elapses
  with no appeal; reverts while appealed, before `executableAt`, after
  expiry, or above the on-chain caps.
- **On-chain slash caps, evaluated at EXECUTION time** against the
  agent's then-current `stakeOf`: ghosting may slash up to **100% of
  stake**, quality misses up to **50%**. Stake can move between proposal
  and execution (top-ups raise the cap; prior slash executions lower it);
  a proposal that becomes unexecutable reverts `ExceedsCap` and must be
  re-proposed — fail-closed, no partial application. The 7-day unstake
  timelock exceeds the appeal window, so a rage-quit cannot dodge a
  pending ruling.

Slashed proceeds split per the ratified subsidy-extraction invariant:
the platform's unrecouped sponsored front (`treasuryCut`, quoted
read-only from the sponsored ledger) goes to the platform treasury first;
the remainder becomes poster re-auction credit (ledger-only, 1:1 backed
by tokens held in the contract — same pattern as
`ExecutionEscrowManager.posterReAuctionBalances`).

**Residual trust assumption (read before deploying).** Adjudicator and
appealAuthority are still trusted roles. If they **collude**, they can
uphold an unjust slash — but it is now **capped** (100%/50% of current
stake, enforced on-chain, not a label), **evidence-committed** (the
evidenceHash is in the signed digest and both proposal and resolution
events name it), **visible** (proposal + window are on-chain and
timestamped), and **delayed** (at least APPEAL_WINDOW before execution).
This is strictly better than the pre-J1 design (one signature, instant,
uncapped, evidenceless) but it is NOT trustless. The demo adjudicator is
a founder-held EOA; mainnet reserves a decentralized adjudicator, and a
natural next step is making the appealAuthority a multisig or timelocked
council rather than an EOA.

Reference: `contracts/StakeSlashManager.sol`,
`contracts/SECURITY_NOTES_J1.md` (adversarial self-review, J1 branch
`xioix/j1-adjudicator-hardening`).

## Prerequisites (founder decisions)

- [ ] **Pin the AXM collateral token address** on Base Sepolia (and later
      Base mainnet). The constructor takes it as an argument; it cannot be
      changed after deploy. Note: `APPEAL_BOND` defaults to 0.01 ether
      units, calibrated for 18-decimal collateral like AXM — non-18-decimal
      collateral MUST pass an explicit `_appealBond`.
- [ ] **Choose the platform treasury address** that receives senior
      clawback cuts.
- [ ] **Choose the admin address** (can only call `setMinStakeWei` —
      minimal privilege by design).
- [ ] **Choose the adjudicator address.** For the demo: the dedicated
      Sepolia adjudicator address on file. The founder holds this key — it
      is never shared, never committed, never pasted anywhere except the
      local signing step below.
- [ ] **Choose the appealAuthority address.** For the demo this may be a
      second founder-held EOA; mainnet targets a multisig/council. The
      founder holds this key under the same handling rules as the
      adjudicator key.
- [ ] **Choose `minStakeWei`** (ratified default: 50% of bid value is
      enforced off-chain per-bid; the on-chain floor is a backstop).
- [ ] **Confirm `appealWindow` / `appealBond`** (defaults: 1 day / 0.01
      collateral units). Both are immutable after deploy; raising the bond
      prices out griefers but also small honest agents — a founder
      calibration call.
- [ ] **Fund the deployer wallet** on Base Sepolia with enough ETH for
      deployment (~2–5M gas).

## Ceremony steps (Base Sepolia first)

1. **Compile fresh.** `python3 scripts/gen_stake_slash_abi.py` (solc
   0.8.24, `--via-ir`, optimizer runs 200). Confirm the emitted bytecode
   hash matches the CI-tested build.
2. **Dry-run the constructor.** Run
   `pytest tests/pytest/test_stake_slash_bridge.py` — 12/12 must be green
   on the exact source being deployed.
3. **Deploy.** From an offline or hardware-backed deployer:
   ```
   StakeSlashManager.deploy(
       admin, adjudicator, axmToken, platformTreasury, minStakeWei,
       7 days,            // unstakeTimelock
       appealAuthority,
       1 days,            // appealWindow (0 = default 1 day)
       0                  // appealBond (0 = default 0.01 collateral units)
   )
   ```
   via `forge create` or the repo's deploy script pattern. Record the
   deployed address, deploy tx hash, block number, and constructor args.
4. **Verify.** Verify source on Basescan (and Sourcify) so the ruling
   digest and the staged-flow semantics are publicly auditable.
5. **Smoke test on Sepolia** (test AXM or a mock first — never the live
   token on the first run):
   - approve + `stake()` → `stakeOf` and `Staked` event correct;
   - `requestUnstake` → `finalizeUnstake` before 7 days reverts,
     after 7 days succeeds;
   - build a ruling with `stake_bridge.SlashRuling` **including a
     non-zero `evidence_hash`** (keccak256 of the evidence bundle), sign
     the `slash_struct_hash` digest with the adjudicator key
     (**canonicalize to low-S** — see below), submit `proposeSlash()` from
     any relayer → `SlashProposed` event with `executableAt` ≈ now + 1
     day; proposing with zero evidence reverts (`MissingEvidence`);
   - `executeSlash()` before the window reverts
     (`AppealWindowNotElapsed`); after the window (and no appeal) it
     succeeds → `SlashExecuted` event, treasury cut and poster credit
     correct;
   - re-proposing the same ruling reverts (`BadNonce` — nonces are
     consumed at propose time); double `executeSlash` reverts
     (`AlreadyExecuted`);
   - appeal path: `appealSlash()` from the agent before the window (with
     bond approval) → `AppealFiled`; `executeSlash` while appealed
     reverts; `resolveAppeal(uphold=true)` from the appealAuthority
     executes the slash and refunds the bond; `resolveAppeal(uphold=false)`
     cancels the ruling with no slash and refunds the bond;
   - cap path: a quality ruling for more than 50% of current stake
     reverts `ExceedsCap` at execution;
   - `setAdjudicator` / `rotateAppealAuthority` from anyone but the
     current holder → reverts; old-adjudicator rulings stop verifying
     after rotation.
6. **Wire the backend.** Set on Railway / the liveness-runner host:
   - `STAKE_SLASH_ADDRESS=<deployed address>`
   - `STAKE_RPC_URL=<Base Sepolia RPC>`
   - `STAKE_CHAIN_ID=84532`
   - `SINCOR_ADJUDICATOR_ID=<adjudicator agent id>` (existing)
   - `STAKE_APPEAL_AUTHORITY=<appeal authority address>`
   Then `dry_run_onchain_deposit` must return green before any real
   deposit is built.
7. **Monitor.** Watch `SlashProposed`, `SlashExecuted`, `AppealFiled`,
   `AppealResolved`, `AdjudicatorRotated`, `AppealAuthorityRotated`,
   `MinStakeUpdated` events; alert on any proposal not preceded by a
   recorded adjudicator ruling in the ops log, and on any appeal filed
   (it needs the appealAuthority's timely attention).

## The low-S rule (read this before signing anything)

`StakeSlashManager._recover` rejects high-S signatures (ECDSA malleability
guard — the same class wave 6 closed in the Python caller-ownership
layer). Common signers (e.g. `eth_account`) do **not** canonicalize `s`,
so roughly half of naive signatures revert on-chain with `BadSignature`.

**The adjudicator's signing flow MUST pass every ruling signature through
`stake_bridge.canonicalize_signature()`** (flips `s → n−s`, toggles `v`)
before submitting. The test suite proves a canonicalized signature
recovers to the adjudicator on-chain and a malleated twin reverts.

## Rotation procedure

If the adjudicator key is ever suspected compromised, or for the planned
mainnet decentralization:

1. From the **current** adjudicator key, call
   `setAdjudicator(<new address>)`. Only the current adjudicator can do
   this; anyone else reverts.
2. Confirm the `AdjudicatorRotated` event and the new `adjudicator()`
   value.
3. Old-adjudicator rulings stop verifying immediately (proven in tests);
   in-flight proposals signed by the old key must be re-signed by the new
   key (nonces are per-agent and were already consumed at propose time —
   the proposal itself is unaffected, only new proposals need the new
   key).

The appealAuthority rotates independently via `rotateAppealAuthority`
(callable only by the current authority).

## Mainnet notes (deferred)

- The demo adjudicator is a founder-held single key — disclosed as such,
  same as the auction demo. Mainnet reserves the decentralized
  adjudicator design (open item), and the appealAuthority should become a
  multisig or timelocked council rather than an EOA.
- Re-run the full ceremony on Base mainnet with the pinned AXM address,
  re-verify sources, and re-run the smoke matrix before wiring
  production traffic.
- Drawing down `posterReAuctionCredits` (poster fast-path) is a
  follow-up work item; the credits accrue but cannot be spent yet.

## What is explicitly NOT authorized here

Broadcasting the deployment, funding agent stakes, arming the fee
conversion executor, or changing any production auth/env — each needs its
own explicit founder go-ahead.
