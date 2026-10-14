# Fee Conversion Executor — read-only and dry-run guidance

**Live conversion is disabled.** The repository has no canonical release/approval
contract for chain, allowlists, immutable manifest digest, operator approval,
expiry, or receipt schema, and the owner decisions do not approve live treasury
conversion. The legacy `armed` setting and `SINCOR_FEE_EXECUTOR_ARMED` variable
cannot authorize it. `execute_plan(..., dry_run=False)` fails before invoking a
signer or receipt callback.

Planning, unsigned transaction construction, read-only quotes, and explicitly
requested dry-run simulation remain available. Use only synthetic obligations
and fake/stubbed RPC and callback dependencies in tests. Do not use a real
forwarder, signer, wallet, broadcast, custody action, or production provider.

The executor may not be live-enabled by changing configuration or following a
previous arming procedure. Reconsideration requires a separately reviewed
canonical authorization interface and explicit owner decisions for capability,
chain/token/target allowlists, limits, approvers/quorum, and canonical receipt
ownership.

## CT-01 scope boundary

- **Fee conversion:** unconditionally denied by this executor before any
  signer, receipt, or broadcast callback.
- **P24:** `onchain/script/07_DeployP24.s.sol` is local simulation only. Its
  ephemeral addresses are not on-chain deployment evidence and it emits no P24
  deployment manifest, receipt, or verification claim.
- **Outside this patch:** other repository deployment scripts and operational
  workflows have not been reviewed or contained here. **CT-01 is not complete**
  and this change does not claim repository-wide deployment safety.

## Canonical authorization interface (2026-10-14, branch xioix/fee-executor-sepolia-dryrun)

The interface the runbook required now exists: `src/sincor2/onchain/fee_authorization.py`.

- `AuthorizationRecord`: capability (`sincor.fee-conversion.v1`), chain
  allowlist, token-in/out allowlists, treasury target, `max_wei_per_run`,
  `max_slippage_bps`, `not_before`/`expires_at`, unique `nonce`,
  approver set + `quorum`, `receipt_owner`.  Addresses normalized; quorum,
  expiry ordering, and limits validated at construction.
- Signatures are EIP-191 personal_sign over
  `keccak256("SINCOR-FEE-AUTH/V1" || canonical_json)`.  Verification is pure
  (no network, no keys): capability, time window, then quorum of DISTINCT
  approver signatures.  A Safe-as-approver needs an EIP-1271 extension —
  documented, not built.
- `check_plan_scope` enforces the CONCRETE plan (chain, tokens, treasury,
  amount, slippage) against the record.  `authorized_dry_run` runs
  verify -> plan -> scope-check -> dry-run simulation -> ledger `authorized`
  -> manifest.  It takes NO signer/broadcast callback: it cannot broadcast
  even if called wrong.  `execute_plan(dry_run=False)` still refuses
  unconditionally.
- Replay: optional caller-owned `used_nonces` journal; without it the
  verifier is stateless by design (documented, tested).

**What this does NOT change:** no owner-signed record exists, no broadcast
ceremony exists, nothing is armed.  Live conversion remains disabled; the
interface only makes a future owner decision verifiable.  Tests use
deterministic synthetic keys; no real key material anywhere.

## Base Sepolia dry-run (2026-10-14, read-only, no broadcast)

Script: `scripts/fee_executor_sepolia_dryrun.py`.  Evidence:
`docs/ops/forksim-evidence/sepolia_dryrun_2026-10-14.json`.  Verdict: `DRY_RUN_OK`.

- Live chain state via `https://sepolia.base.org` (chain 84532, block 48114000).
  V4 infra verified present by `eth_getCode`: PoolManager
  `0x05E73354cFDd6745C338b50BcFDfA3Aa6fA03408`, V4Quoter
  `0x4a6513c898fe1B2d0E78d3b0e0A4a151589B1cBa`, UniversalRouter
  `0x492E6456D9528771018DeB9E87ef7750EF184104`, Permit2 (canonical), WETH
  (predeploy), Circle testnet USDC `0x036CbD53842c5426634e7929541eC2318f3dCF7e`.
  (Sepolia V4 addresses differ from Base mainnet; verified on-chain, not
  trusted from docs.)
- `discover_pool` found WETH->USDC 3000/60.  Live quote: 0.01 WETH ->
  99,697 USDC-wei.  Quote scaling probes confirm the pool holds only ~$0.10
  of liquidity (dust pool) — sufficient to prove the machinery, useless for
  real conversions.  **The AXM fee token is not deployed on Sepolia; the AXM
  leg cannot be quoted there by construction.**
- Full unsigned-tx plan built (approve_permit2, permit2_approve, swap,
  forward_to_treasury), all targeting Sepolia addresses with chainId 84532.
- Authorization verified end-to-end with synthetic quorum keys; tampered
  record rejected (negative control).
- Simulations: both approvals passed; the swap simulation failed closed on
  the unfunded synthetic forwarder — the predicted, designed outcome (no
  funds moved; none exist).  The swap-execution leg was previously proven on
  a Base-mainnet fork with a funded forwarder (see FEE_EXECUTOR_FORKSIM_READINESS.md).

**Still founder-gated before any real movement:** a real owner-signed
AuthorizationRecord with real quorum keys; a broadcast ceremony (does not
exist in this repo); real V4 liquidity for the fee token; reconciliation of
queued obligations against receipts.  None of these are created or implied
by this work.
