# SigilPolicyModule — ERC-7579 pre-settlement policy module CANDIDATE

**Status: candidate for co-development with ZeroDev. Not deployed, not
installed on any production account, not a product.** Nothing in this folder
has been audited. Do not represent it otherwise.

## What it is

An ERC-7579 **Hook module (type 4)** that gates fund release from a modular
smart account on a valid **SIGIL attestation**: an EIP-712 (SEAL-style) signed
commitment from a configured verifier key binding

```
(account, taskSpecHash, resultCommitment, payee, token, amountCap, expiry, nonce)
```

Flow:

1. The account installs the module with
   `onInstall(abi.encode(verifier, maxCap, unverifiedThreshold, settlementHub))`.
2. When a SIGIL verifier is satisfied a task's result matches its spec, it
   signs an attestation. Anyone may relay it to `registerAttestation`
   (the signature is the security, not the relayer).
3. When the account executes a payment, the account calls the hook's
   `preCheck` first (per the ERC-7579 hook extension). The hook decodes the
   ERC-7579 packed single call, detects a fund release (native value, ERC20
   `transfer`, or `transferFrom` out of the account), and:
   - if the **decoded amount** is at or below the account's
     `unverifiedThreshold`, the release passes ungated (micro-settlements);
   - otherwise it matches a pending unexpired attestation for that exact
     (token, payee), checks the amount against the attestation cap, checks
     the optional Settlement Hub record (below), **consumes** the
     attestation, and only then lets execution proceed.

Blocked, by construction and by test: missing attestation, expired
attestation, replayed digest or nonce, wrong signer, amount over the
attestation cap, attestation cap over the install-time policy cap, payee
mismatch, attestation bound to a different account, and (hub configured)
no recorded commitment for the task.

Non-release calls (zero value, no transfer semantics) are not gated.

## The threshold measures the DECODED amount — never msgValue

The founder spec-drop draft hook (`SincorSigilHook.sol`, 2026-11-11)
compared the hook's `msgValue` against its unverified threshold. That is
broken for the asset that matters: **USDC moves via ERC-20
`transfer`/`transferFrom` executions, where `msgValue` is 0 ETH** — the
threshold would wave through token settlements of any size. This module
decodes (token, recipient, amount) from the execution calldata for the
`transfer` (0xa9059cbb) and `transferFrom` (0x23b872dd) selectors and
applies the threshold to that amount (raw units of the moved asset).
`test_USDCTransferAboveThresholdBlockedWithZeroMsgValue` is the regression
test: 100 USDC, packed value 0, no attestation → blocked.

## How the attestation reaches the hook (no footer)

The draft hook extracted `(taskId, sigilCommitment)` from a 64-byte footer
appended to `msgData`. **That convention does not exist** in ERC-7579,
Kernel, or Safe7579: hooks receive `(msgSender, value, msgData)` where
`msgData` is the execution calldata owned by the account/`execute`
standards — appending foreign bytes would corrupt the inner call or be
stripped in transit. Per-account install data can't carry per-task
commitments either (install happens once). So this module keeps an
**attestation registry keyed by (account, token, payee)**: the
verifier-signed attestation is presented ahead of execution via
`registerAttestation`, and `preCheck` matches it against the (token,
payee, amount) decoded from the actual execution. The hook verifies
against the real payment values, not against a caller-supplied reference.
A validation-phase variant (Kernel type-5 Policy / type-9
PreValidationHook reading the UserOp signature tail, the Kernel
permission convention) is future co-development work.

## Settlement Hub integration — and its exact trust model

`SincorSettlementHub.sol` in this folder is the founder spec-drop Hub
draft, copied in as the interface source of truth with one correction
(**fee 100 → 500 bps**, the locked SINCOR canon). The module's Hub
interface is aligned to the Hub's real signatures — the draft hook's
`verifyAndSettleSeal(bytes32,bytes32,bytes)` does not exist; the Hub's is
`verifyAndSettleSeal(SealAuthorization,bytes32,uint8,bytes32,bytes32)`,
and the hook never calls it (settlement is the facilitator's action).
The hook calls only `isTaskVerified(bytes32 taskId, bytes32 expectedSigil)`.

**Trust model, stated exactly:** the Hub records the SIGIL commitment at
settlement time, submitted by the facilitator after **off-chain**
verification. `isTaskVerified` proves a commitment was **recorded** for
the task — it does **not** prove verification happened on-chain, because
it didn't (the draft Hub also signs `expectedSchemaHash` into the SEAL
but never enforces it on-chain; it records whatever commitment the
settlement submitter passes). With a hub configured, this hook enforces:
"a recorded commitment exists for this task, and a verifier-signed
attestation binds this release to that same task + result commitment."

## What it is NOT

- It does **not** slash anyone and does not hold stake. SINCOR's ghost
  slashing is enforced by `stake_ledger` + `ExecutionEscrowManager`, not
  by a validation or hook module.
- It does **not** judge work quality. The SIGIL verifier makes that
  judgement off-chain; the module only checks the verifier's signed
  commitment is valid, fresh, in-cap, unused — and, when a hub is
  configured, recorded.
- It is **not** a Kernel plugin yet. It implements the standard ERC-7579
  hook interface (`preCheck`/`postCheck`, type 4). Kernel attaches hooks
  to validators/executors/selectors via its HookManager and derives hook
  calldata from the UserOp; a thin Kernel-specific adapter (and ZeroDev
  review) is co-development work, listed in the build report.

## Design notes

- **Consumption in `preCheck`:** single-use is enforced by consuming the
  attestation before execution. If execution then fails, the attestation
  is still burned and the verifier issues a fresh one. Safe direction: a
  release can never happen twice under one attestation.
- **Install gas (MoebiusX lesson):** the first session op that installs a
  validator/hook pays `onInstall` SSTOREs inside EntryPoint validation.
  SINCOR measured CallPolicy V0_0_4 install at 2,486,476 gas vs a 2,000,000
  hardcoded bundler verification limit (fixed with a two-tier gas scheme:
  3.5M install tier, 1M steady). Budget this module's install at the
  install tier, never steady tier.
- **One pending attestation per (account, token, payee):** a fresh
  registration cannot displace a live one (only an expired one).
- **Threshold units:** the threshold compares raw units of the moved
  asset (e.g., 5,000,000 = 5 USDC at 6 decimals; wei for native). One
  number serves all assets in this candidate — per-asset thresholds are a
  co-development refinement.

## Tests

`test/SigilPolicyModule.t.sol` — **31 tests, all passing (2026-11-11)**:
22 attestation/lifecycle/full-loop tests plus 9 spec-drop correction
tests (decoded-amount threshold incl. the msgValue==0 USDC regression,
hub recorded-commitment gate, Hub at canon 500 bps with exact 95/5 split,
hub wrong-signer and expiry reverts).

Run (this repo's full build currently fails on pre-existing missing
`lib/v4-core` / `lib/openzeppelin-contracts-v4` for the Uniswap v4 hook
contracts — see `foundry.toml`; the scoped run below compiles only these
modules and their tests):

```bash
FOUNDRY_SRC=contracts/modules FOUNDRY_TEST=test forge test \
  --match-contract "SigilPolicyModuleTest|SigilThresholdAndHubTest" \
  --skip IntentHook --skip IntentHookV2 --skip BribeLoopHook
```

Requires `lib/forge-std` and `lib/openzeppelin-contracts` (v5) in `lib/`.
