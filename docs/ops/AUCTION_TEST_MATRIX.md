# A2A Auction Live Test Matrix

Operational test matrix for the SINCOR2 sealed-bid auction system on
Base Sepolia (chain ID 84532). Companion to
`docs/architecture/AUCTION_GROUND_TRUTH.md` — the ground truth defines
*what* the system does; this matrix defines *how we prove it* before
any contract touches real funds.

Status: ratified 2026-09-27. Contracts not yet deployed; run this matrix
against the Sepolia rehearsal deployment before mainnet consideration.

Conventions: `eth-tester` + `pytest` + `web3.py`. State-modifying
`.transact()` reverts surface as
`eth_tester.exceptions.TransactionFailed`; view-call reverts surface as
`web3.exceptions.ContractLogicError`.

---

## Phase 1: Pre-Flight Environment & Identity Verification

```python
def test_preflight_domain_and_identity(a2a_contract, bidder_nodes, chain_id=84532):
    # 1. Assert Chain ID is Base Sepolia (84532)
    assert a2a_contract.functions.getChainId().call() == chain_id

    # 2. Verify domain separator parity across offchain bidders and onchain view
    expected_domain_sep = a2a_contract.functions.EIP712_DOMAIN_SEPARATOR().call()
    for node in bidder_nodes:
        assert node.compute_domain_separator() == expected_domain_sep

    # 3. Verify agentIdHash preimage mapping parity across all nodes
    for node in bidder_nodes:
        assert node.agent_id_hash == web3.keccak(text=node.agent_id)

    # 4. Assert key separation across system roles
    adjudicator = a2a_contract.functions.tempAdjudicator().call()
    deployer = a2a_contract.functions.owner().call()
    treasury = a2a_contract.functions.treasury().call()
    assert len({adjudicator, deployer, treasury}) == 3
```

Run at node startup, *before* the commit window opens — catching a
domain-separator or preimage mismatch mid-window wastes the rehearsal.

---

## Phase 2: Sequence-Correct Window, Boundary & Ghost Lifecycle

- **Late Commit:** submit `commit()` past `commitDeadline`. Assert
  `TransactionFailed` matching `CommitWindowClosed()`.
- **Late Reveal Full Lifecycle Test:**
  - *Lock:* commit a bid during the open window; lock 0.50 x bounty
    stake in the offchain state shim.
  - *Late Reveal Revert:* fast-forward EVM time past `revealDeadline`.
    Call `reveal()`. Assert it reverts with `TransactionFailed` matching
    `RevealWindowClosed()`. Verify the offchain stake remains locked
    (not moved or burned yet).
  - *Settlement Ghost Slash:* trigger auction `timeout()` or settlement.
    Verify the un-revealed bidder is identified as a ghost node.
  - *Final Stake Drain:* assert the offchain shim processes a 100% ghost
    slash, moving the entire locked stake directly into
    `posterReAuctionBalances[poster]`.
- **Same-Block Race Resolution:** pack a block with a valid `reveal()`
  and a permissionless `timeout()`.
  - If `reveal()` executes first: `reveal()` succeeds; `timeout()`
    reverts with `TransactionFailed` matching `AuctionAlreadySettled()`.
  - If `timeout()` executes first: `timeout()` processes zero-transfer
    state; `reveal()` reverts with `TransactionFailed` matching
    `AuctionTimedOut()`.

---

## Phase 3: Pinned Rules & Boundary Limits

| Scenario | Deterministic Rule / Execution Logic | Assertion |
|---|---|---|
| Tie-Break Rule | Winner selected by earlier `commit()` timestamp; secondary tie-break goes to lower `agentIdHash` (lexicographical bytes32). | Winner matches deterministic sorting key; loser deposit refunded. |
| Single Bidder | Winning price defaults to full submitted bid (P_bid). No synthetic reserve variable required. | Winner charged P_bid; delta refunded = 0. |
| uint96 Boundary | Submit bid amount 2^96 (uint96.max + 1) through the call path. | Reverts onchain with `TransactionFailed` matching `PriceTooLarge()`. |

---

## Phase 4: Exception Handling & Custom Error Audits

```python
import pytest
from eth_tester.exceptions import TransactionFailed

def test_adjudicator_auth_and_replay(a2a_contract, unauthorized_account,
                                     adjudicator_account):
    task_id = web3.keccak(text="task_001")
    ruling_id = web3.keccak(text="ruling_001")

    # 1. Verify unauthorized caller reverts on transact()
    with pytest.raises(TransactionFailed, match="UnauthorizedAdjudicator"):
        a2a_contract.functions.adjudicate(
            task_id, ruling_id, 1).transact({'from': unauthorized_account})

    # 2. Execute valid adjudication
    a2a_contract.functions.adjudicate(
        task_id, ruling_id, 1).transact({'from': adjudicator_account})

    # 3. Verify replay attempt reverts on transact()
    with pytest.raises(TransactionFailed, match="RulingAlreadyProcessed"):
        a2a_contract.functions.adjudicate(
            task_id, ruling_id, 1).transact({'from': adjudicator_account})
```

Custom error names (`UnauthorizedAdjudicator`, `RulingAlreadyProcessed`,
`StakeAlreadySlashed`, `AuctionAlreadySettled`, `AuctionTimedOut`,
`CommitWindowClosed`, `RevealWindowClosed`, `PriceTooLarge`) must exist
in the contract ABIs — these tests assert against them, so the contracts
must define them.

---

## Phase 5: Realigned Slash Flow & Comprehensive Reconciliation Invariants

### Slash & Challenger Bond Accounting Rules

- **100% Slash Destination:** 100% of all slashed stake proceeds route to
  `posterReAuctionBalances[poster]`.
- **Challenger Bond (0.02 ETH):**
  - *Successful Challenge:* the 0.02 ETH bond is refunded in full to the
    challenger. The slashed stake itself goes 100% to
    `posterReAuctionBalances[poster]`.
  - *Failed Challenge:* the 0.02 ETH bond is forfeited and transferred
    directly into `posterReAuctionBalances[poster]`.

### Onchain vs. Offchain Reconciliation Invariants

- Sum of all offchain `allocated` stake across agents == total stake
  locked onchain (per StakeLedger).
- Per-agent offchain locked stake == that agent's onchain stake balance,
  for every agent with a non-zero position.
- Offchain Pool 2 view == `posterReAuctionBalances[poster]`, for every
  poster with re-auction credit.
- `funded_axm - allocated_axm` == pool `available_axm`, reconciling
  directly with the bounty-pool ledger file.
- Zero orphan allocations: every offchain allocation record references a
  `task_id` that exists on the board.
- **Execution Cadence:** run this reconciliation check after every phase
  (Phase 1 through Phase 6). Any ledger drift caught mid-suite fails the
  test run instantly.

---

## Phase 6: Base Sepolia Gas Profiling (n=10)

Target network: Base Sepolia (chain ID 84532). Bounds parameterized for
n <= 10 bidders in `selectWinnerAndFund()` (bounded O(n) array iteration).

```
- commit()                  : < 48,000 gas
- reveal()                  : < 65,000 gas
- selectWinnerAndFund(n=10) : < 120,000 gas
- adjudicate()              : < 72,000 gas
- timeout()                 : < 35,000 gas
```

---

## Execution & Verification Commands

```bash
# 1. Run complete A2A auction test suite with per-phase reconciliation checks
pytest tests/pytest/test_a2a_auction.py -v -s --tb=short

# 2. Run standalone reconciliation and ghost lifecycle assertions
pytest tests/pytest/test_a2a_auction.py -k "test_reconciliation or test_late_reveal_ghost_lifecycle" -vv

# 3. Profile Base Sepolia gas targets against active testnet RPC fork
BASE_SEPOLIA_RPC_URL=$BASE_SEPOLIA_RPC pytest tests/pytest/test_a2a_gas_profiling.py -v -s
```
