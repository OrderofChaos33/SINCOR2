# SincorValidationReputationOracle — ERC-8004-style (draft)

**Status: ERC-8004-STYLE, DRAFT. Not conformance to any final ERC-8004 spec.
Not deployed anywhere. Not audited. Local Foundry only.**

Implements Validation + Reputation registries driven by SIGIL settlements,
plus a minimal Identity registry STUB (`SincorIdentityRegistryStub`:
register/check only — no metadata, transfer, or wallet logic).

## Flow
1. Settlement is verified/recorded in a SincorSettlementHub-style source:
   `isTaskVerified(taskId, sigilCommitment)` returns true.
2. An authorized writer (the configured settlementSource hub address, or the
   owner) calls `recordVerifiedSettlement(agent, taskId, sigil, outcome)`.
3. The oracle checks the hub, records an attestation
   (agent, taskId, sigilCommitment, outcome, timestamp), and updates reputation.
4. Replay of the same (taskId, sigilCommitment) reverts and never double-counts.

`recordValidation(...)` is the writer-asserted path: same writers, no hub check.

## Reputation (deterministic)
Success +10, Failure -10, score bounded to [-100, 1000].
Counts of successes/failures are stored alongside the score.

## Trust assumptions (for adversarial review)
- The oracle does NOT verify SIGIL output on-chain. Verification is off-chain
  (facilitator), exactly as in the reference SincorSettlementHub draft, which
  RECORDS a commitment submitted at settlement; `isTaskVerified` proves a
  commitment was recorded, not that verification happened on-chain. The Hub
  draft also signs expectedSchemaHash but never enforces it on-chain.
- recordVerifiedSettlement trusts the configured settlementSource's answer.
  Whoever controls that address (or the owner, who can rotate it) controls
  the verified path. Owner rotation is single-step, no timelock.
- recordValidation trusts the writer's outcome assertion entirely.
- The settlementSource is a single address: if it is an EOA, its key is the
  whole write security for hub-path calls; if it is the Hub contract, the Hub
  itself never calls this oracle in the reference design — integration wiring
  (a caller contract or facilitator EOA set as source) is future work.
- Reputation is a bounded counter, not a quality judgement or slashing input.

## Tests
test/SincorValidationReputationOracle.t.sol — 12 tests.
Scoped run (repo-wide forge build fails pre-existing on missing
lib/v4-core / lib/openzeppelin-contracts-v4 for Uniswap v4 hooks — not fixed here):
  FOUNDRY_SRC=contracts/erc8004 FOUNDRY_TEST=test forge test \
    --match-contract SincorValidationReputationOracleTest \
    --skip IntentHook --skip IntentHookV2 --skip BribeLoopHook
