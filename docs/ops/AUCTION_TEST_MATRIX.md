# A2A Auction Live Test Matrix

Operational test matrix for the SINCOR2 sealed-bid auction system on
Base Sepolia (chain ID 84532). Companion to
`docs/architecture/AUCTION_GROUND_TRUTH.md` — the ground truth defines
*what* the system does; this matrix defines *how we prove it* before
any contract touches real funds.

Status: **rewritten 2026-09-29 against the reviewed ABI** (compiled with
solc 0.8.24, optimizer 200 runs, viaIR). Every function name, parameter
list, and custom error below was verified against actual compiler
output. This supersedes the 2026-09-27 version, which referenced ABI
elements that do not exist (`getChainId()`, `EIP712_DOMAIN_SEPARATOR()`,
`tempAdjudicator()`, `treasury()`, `adjudicate()`, `AuctionAlreadySettled`,
`AuctionTimedOut`, `UnauthorizedAdjudicator`, `RulingAlreadyProcessed`,
`StakeAlreadySlashed`, and a secondary `agentIdHash` tie-break).

Contracts not yet deployed; run this matrix against the Sepolia rehearsal
deployment before mainnet consideration.

Conventions: `eth-tester` + `pytest` + `web3.py`. State-modifying
`.transact()` reverts surface as
`eth_tester.exceptions.TransactionFailed`; view-call reverts surface as
`web3.exceptions.ContractLogicError`.

**Signature-layer note (read before Phase 1):** the onchain commit scheme
is plain keccak — `reveal()` checks
`keccak256(abi.encodePacked(bytes32(price), salt, agentIdHash))`. There is
**no EIP-712 in the contracts**. EIP-712 lives offchain: the Python
bidder client / auction relayer (`src/sincor2/onchain/`) signs bid
authorizations with `eth_account`, proven byte-identical by
`tests/pytest/test_eip712_differential.py`. Do not describe the onchain
commit/reveal as "EIP-712" — it is keccak commit/reveal with EIP-712-signed
offchain bid authorizations.

---

## Phase 1: Pre-Flight Environment & Identity Verification

```python
def test_preflight_domain_and_identity(w3, auction, escrow, bidder_nodes, chain_id=84532):
    # 1. Chain ID is Base Sepolia (84532) — client-side check; the contracts
    #    expose no getChainId() view.
    assert w3.eth.chain_id == chain_id

    # 2. Commit-scheme parity: offchain preimage computation must match what
    #    reveal() accepts onchain. EIP-712 digests are offchain-only (see
    #    note above); assert byte-identical digests across all bidder nodes
    #    via the Python bridge, not via a contract view.
    for node in bidder_nodes:
        assert node.compute_commitment(price, salt, agent_id_hash) == \
            w3.solidity_keccak(["uint256", "bytes32", "bytes32"],
                               [price, salt, agent_id_hash])

    # 3. agentIdHash preimage mapping parity across all nodes (offchain
    #    convention: keccak256(agent_id)).
    for node in bidder_nodes:
        assert node.agent_id_hash == w3.keccak(text=node.agent_id)

    # 4. Key separation across system roles — real views:
    #    owner() (auction), adjudicator() (escrow), guardianMultisig().
    owner = auction.functions.owner().call()
    adjudicator = escrow.functions.adjudicator().call()
    guardian = auction.functions.guardianMultisig().call()
    assert len({owner, adjudicator, guardian}) == 3
```

Run at node startup, *before* the commit window opens — catching a
preimage or key-role mismatch mid-window wastes the rehearsal.

---

## Phase 2: Sequence-Correct Window, Boundary & Ghost Lifecycle

- **Late Commit:** submit `commit(auctionId, commitHash)` past
  `commitDeadline`. Assert `TransactionFailed` matching
  `CommitWindowClosed()`.
- **Late Reveal:** fast-forward EVM time past `revealDeadline`.
  Call `reveal(auctionId, price, salt, agentIdHash)`. Assert it reverts
  with `TransactionFailed` matching `RevealWindowClosed()`. Unrevealed
  commits carry no bonds — the auction-level `timeout(auctionId)` simply
  finalizes and ignores them (`TimedOut` event; no refunds, nothing at
  stake at this layer).
- **Ghost Slash (onchain proof):** run the full flow —
  `selectWinnerAndFund` → `depositStake` (50% of bid per `minStakeBps`) →
  worker never calls `submitResult` → fast-forward past
  `executionDeadline` → permissionless `escrow.timeout(auctionId)`.
  Assert: escrow state `Slashed`, **100% of `agentStake` plus
  `creditBacking` credited to `posterReAuctionBalances[poster]`**,
  `SlashExecuted` with `SlashReason.ExecutionGhosting`, poster's ETH
  refunded. This is the onchain ghost-slash mirror — the money path the
  offchain stake ledger mirrors.
- **No-Stake Timeout:** `selectWinnerAndFund` → worker never stakes →
  fast-forward past `stakeDepositDeadline` → `escrow.timeout(auctionId)`.
  Assert: state `TimedOut`, poster refunded in full, **no slash**
  (nothing was ever at risk).
- **Finalization Race (the real one):** `reveal()` and auction
  `timeout()` are mutually exclusive by timestamp (`reveal` requires
  `ts <= revealDeadline`; `timeout` requires `ts > revealDeadline`),
  so they cannot race. The real race is poster
  `selectWinnerAndFund()` vs permissionless `timeout()` — both valid
  after `revealDeadline`. Whichever finalizes first wins; the loser
  reverts with `TransactionFailed` matching `AlreadyFinalized()`.
  Test both orderings.

---

## Phase 3: Pinned Rules & Boundary Limits

| Scenario | Deterministic Rule / Execution Logic | Assertion |
|---|---|---|
| Tie-Break Rule | `vickreyResult` iterates `bidders[auctionId]` in commit order with strict `<`: **ties break to the earliest committer**. There is no secondary `agentIdHash` tie-break. | Winner matches earliest-committer ordering; loser deposits refunded. |
| Single Bidder | `second` stays `type(uint256).max`; falls back to the sole bid (first-price). | Winner charged P_bid; delta refunded = 0. |
| uint96 Boundary | Submit bid amount 2^96 (uint96.max + 1) through the reveal path. | Reverts onchain with `TransactionFailed` matching `PriceTooLarge()`. |

---

## Phase 4: Dispute & Adjudication Guards

The adjudication flow is `openQualityDispute` /
`resolveQualityDispute` on `ExecutionEscrowManager` — there is no
`adjudicate(task_id, ruling_id, ...)` function.

```python
import pytest
from eth_tester.exceptions import TransactionFailed

def test_dispute_guards(escrow, auction_id, poster, challenger, adjudicator, stranger):
    bond = escrow.functions.challengerBond().call()

    # 1. Poster disputes free — but must send zero value.
    with pytest.raises(TransactionFailed, match="FundingMismatch"):
        escrow.functions.openQualityDispute(
            auction_id, batch_digest).transact({"from": poster, "value": 1})
    escrow.functions.openQualityDispute(
        auction_id, batch_digest).transact({"from": poster})

    # 2. Challenger below the bond reverts.
    with pytest.raises(TransactionFailed, match="InsufficientBond"):
        escrow.functions.openQualityDispute(
            auction_id, batch_digest).transact({"from": stranger, "value": bond - 1})

    # 3. Only the adjudicator resolves; anyone else reverts.
    with pytest.raises(TransactionFailed, match="Unauthorized"):
        escrow.functions.resolveQualityDispute(
            auction_id, True).transact({"from": stranger})

    # 4. Valid resolution, then replay reverts (dispute deleted).
    escrow.functions.resolveQualityDispute(
        auction_id, True).transact({"from": adjudicator})
    with pytest.raises(TransactionFailed, match="InvalidState"):
        escrow.functions.resolveQualityDispute(
            auction_id, True).transact({"from": adjudicator})

    # 5. Adjudicator-only setters reject strangers.
    with pytest.raises(TransactionFailed, match="Unauthorized"):
        escrow.functions.setChallengerBond(bond).transact({"from": stranger})
    with pytest.raises(TransactionFailed, match="Unauthorized"):
        escrow.functions.setAdjudicator(stranger).transact({"from": stranger})
```

Dispute economics (asserted in the economics tests, not just guards):
- **Upheld (`slashWorker=true`):** 50% of `agentStake` → 
...[truncated 4189 chars]
`posterReAuctionBalances[poster]`; challenger's bond refunded in full;
worker keeps the unslashed half of their stake.
- **Rejected (`slashWorker=false`):** challenger's bond (if any) →
  `posterReAuctionBalances[poster]` as the anti-griefing penalty; worker
  paid `bidAmount + agentStake` in full.
- **Dark adjudicator:** dispute open past `disputeTimestamp +
  adjudicationWindowDuration` → permissionless `timeout()` pays the
  worker (optimistic default) and refunds the challenger's bond. Liveness
  over safety; silence is not the challenger's fault.

---

## Phase 5: Slash Flow & Reconciliation Invariants

### Slash & Challenger Bond Accounting Rules

- **Ghost slash (execution ghosting):** 100% of `agentStake` +
  `creditBacking` → `posterReAuctionBalances[poster]`, via
  `escrow.timeout()` case 2. This is the onchain mirror of the offchain
  stake ledger's 100% ghosting slash.
- **Quality-miss slash:** 50% of `agentStake` →
  `posterReAuctionBalances[poster]`, via
  `resolveQualityDispute(auctionId, true)`. Worker keeps the other half.
- **Challenger bond** (deployment parameter; 0.02 ETH per ratified
  config, readable via `challengerBond()`):
  - *Upheld dispute:* bond refunded in full to the challenger.
  - *Rejected dispute:* bond forfeited into
    `posterReAuctionBalances[poster]`.
  - *Poster disputes free* (`msg.value` must be 0) — no bond at all.

### Onchain vs. Offchain Reconciliation Invariants

- Sum of all offchain `allocated` stake across agents == total stake
  locked onchain (per `depositStake` records).
- Per-agent offchain locked stake == that agent's onchain stake balance,
  for every agent with a non-zero position.
- Offchain Pool 2 view == `posterReAuctionBalances[poster]` (also
  readable via `getPosterReAuctionBalance(poster)`), for every poster
  with re-auction credit.
- `funded_axm - allocated_axm` == pool `available_axm`, reconciling
  directly with the bounty-pool ledger file.
- Zero orphan allocations: every offchain allocation record references a
  `task_id` that exists on the board.
- **Execution Cadence:** run this reconciliation check after every phase
  (Phase 1 through Phase 6). Any ledger drift caught mid-suite fails the
  test run instantly.

---

## Phase 6: Base Sepolia Gas Profiling

Measured on eth-tester (PyEVM, solc 0.8.24, optimizer 200 runs, viaIR),
2026-09-29. Bounds are measured value + ~25% headroom, rounded — they are
regression tripwires, not L2 fee estimates (Base gas pricing differs).
`selectWinnerAndFund` scales ~+6.9k gas per revealed bidder
(178,661 at n=1 → 240,713 at n=10).

```
- openAuction                    measured   70,721  bound <   90,000
- commit                         measured  100,515  bound <  125,000  (first; 83,415 at 10th)
- reveal                         measured   76,087  bound <   95,000
- selectWinnerAndFund (n=10)     measured  240,713  bound <  300,000
- auction timeout()              measured   30,249  bound <   40,000
- depositStake                   measured   39,189  bound <   50,000
- escrow timeout() ghost slash   measured   78,397  bound <  100,000
- escrow timeout() no-stake      measured   52,610  bound <   70,000
- submitResult                   measured   78,558  bound <  100,000
- openQualityDispute             measured  105,647  bound <  135,000
- resolveQualityDispute (slash)  measured   85,963  bound <  110,000
- resolveQualityDispute (reject) measured   67,984  bound <   85,000
```

Enforced by `tests/pytest/test_a2a_gas_profiling.py` (5 tests, green
2026-09-29). Re-run after any contract change; a bound breach means the
change altered the money path and needs review before deployment.

---

## Execution & Verification Commands

Self-contained suites (no app imports): run with `--noconftest` using a
venv with eth-tester/py-evm/web3/py-solc-x (solc 0.8.24 at
`~/.solcx/solc-v0.8.24`).

```bash
# 1. Full onchain matrix: escrow guards + selection bridge + Python bridge (63 tests)
~/.venvs/sincor2/bin/python -m pytest tests/pytest/test_execution_escrow.py \
  tests/pytest/test_selection_bridge.py tests/pytest/test_auction_bridge.py \
  -q --noconftest -p no:cacheprovider

# 2. Gas regression tripwires (5 tests)
~/.venvs/sincor2/bin/python -m pytest tests/pytest/test_a2a_gas_profiling.py \
  -q --noconftest -p no:cacheprovider

# 3. EIP-712 offchain digest differential (byte-identical eth_account digests)
~/.venvs/sincor2/bin/python -m pytest tests/pytest/test_eip712_differential.py \
  -q --noconftest -p no:cacheprovider
```

All green 2026-09-29: 72 tests (58 onchain + 5 gas + 9 EIP-712 differential). The Sepolia rehearsal
deployment replays Phases 1–5 against chain ID 84532 with the same
assertions; Phase 6 bounds are re-baselined on Sepolia before mainnet
consideration.
