# StakeSlashManager — Deploy Ceremony Runbook

**Status: NOT DEPLOYED.** The contract (`contracts/StakeSlashManager.sol`)
is designed, compiled (solc 0.8.24), and proven on eth-tester
(`tests/pytest/test_stake_slash_bridge.py`, 12/12 green). The Python wiring
(`src/sincor2/onchain/stake_bridge.py`, `stake_ledger.py` onchain section)
is dry-run only. Nothing here moves funds until the founder explicitly
authorizes the ceremony below and the deploy wallets are funded.

## What it is

On-chain stake deposits and adjudicator-ruled slashing for the sealed-bid
task market. Agents stake AXM (ERC-20 `transferFrom` pull); slashing
executes only from an adjudicator-signed EIP-191 ruling that binds
contract address + chainid + agent + poster + amount + treasury cut +
nonce + expiry + reason. Slashed proceeds split per the ratified
subsidy-extraction invariant: the platform's unrecouped sponsored front
(`treasuryCut`, quoted read-only from the sponsored ledger) goes to the
platform treasury first; the remainder becomes poster re-auction credit
(ledger-only, 1:1 backed by tokens held in the contract — same pattern as
`ExecutionEscrowManager.posterReAuctionBalances`).

Adjudicator pattern mirrors `ExecutionEscrowManager`: single `adjudicator`
address, `setAdjudicator` rotation callable only by the current
adjudicator, zero-address guarded.

## Prerequisites (founder decisions)

- [ ] **Pin the AXM collateral token address** on Base Sepolia (and later
      Base mainnet). The constructor takes it as an argument; it cannot be
      changed after deploy.
- [ ] **Choose the platform treasury address** that receives senior
      clawback cuts.
- [ ] **Choose the admin address** (can only call `setMinStakeWei` —
      minimal privilege by design).
- [ ] **Choose `minStakeWei`** (ratified default: 50% of bid value is
      enforced off-chain per-bid; the on-chain floor is a backstop).
- [ ] **Confirm the demo adjudicator EOA**: the dedicated Sepolia
      adjudicator address on file. The founder holds this key — it is
      never shared, never committed, never pasted anywhere except the
      local signing step below.
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
       admin, adjudicator, axmToken, platformTreasury, minStakeWei, 7 days
   )
   ```
   via `forge create` or the repo's deploy script pattern. Record the
   deployed address, deploy tx hash, block number, and constructor args.
4. **Verify.** Verify source on Basescan (and Sourcify) so the ruling
   digest and `slash()` semantics are publicly auditable.
5. **Smoke test on Sepolia** (test AXM or a mock first — never the live
   token on the first run):
   - approve + `stake()` → `stakeOf` and `Staked` event correct;
   - `requestUnstake` → `finalizeUnstake` before 7 days reverts,
     after 7 days succeeds;
   - build a ruling with `stake_bridge.SlashRuling`, sign the
     `slash_struct_hash` digest with the adjudicator key (**canonicalize
     to low-S** — see below), submit `slash()` from any relayer →
     `SlashExecuted` event, treasury cut and poster credit correct;
   - replay the same ruling → reverts (`BadNonce`);
   - `setAdjudicator` from a non-adjudicator → reverts.
6. **Wire the backend.** Set on Railway / the liveness-runner host:
   - `STAKE_SLASH_ADDRESS=<deployed address>`
   - `STAKE_RPC_URL=<Base Sepolia RPC>`
   - `STAKE_CHAIN_ID=84532`
   - `SINCOR_ADJUDICATOR_ID=<adjudicator agent id>` (existing)
   Then `dry_run_onchain_deposit` must return green before any real
   deposit is built.
7. **Monitor.** Watch `SlashExecuted`, `AdjudicatorRotated`,
   `MinStakeUpdated` events; alert on any `slash()` not preceded by a
   recorded adjudicator ruling in the ops log.

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
   in-flight rulings signed by the old key must be re-signed by the new
   key (nonces are per-agent and survive rotation).

## Mainnet notes (deferred)

- The demo adjudicator is a founder-held single key — disclosed as such,
  same as the auction demo. Mainnet reserves the decentralized
  adjudicator design (open item).
- Re-run the full ceremony on Base mainnet with the pinned AXM address,
  re-verify sources, and re-run the smoke matrix before wiring
  production traffic.
- Drawing down `posterReAuctionCredits` (poster fast-path) is a
  follow-up work item; the credits accrue but cannot be spent yet.

## What is explicitly NOT authorized here

Broadcasting the deployment, funding agent stakes, arming the fee
conversion executor, or changing any production auth/env — each needs its
own explicit founder go-ahead.
