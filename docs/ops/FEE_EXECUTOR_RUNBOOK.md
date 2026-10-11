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
