# SINCOR2 Sealed-Bid Auction — Ground-Truth Specification

**Status:** canonical. This document is the authoritative description of the
sealed-bid auction architecture. It was verified line-by-line against
`contracts/CommitRevealAuction.sol`, `contracts/ExecutionEscrowManager.sol`,
`contracts/IExecutionEscrowManager.sol`, `src/sincor2/onchain/stake_ledger.py`,
and the platform relayer. If any other doc, comment, or message disagrees
with this file, this file wins — fix the other one.

**Scope:** the sealed-bid auction system as built (Python offchain path +
Base L2 onchain path). Settlement currency, price discovery, credit pools,
and the exact contract interface below are locked; changes require a new
ratified decision record.

---

## System invariants

- **Settlement currency:** Native ETH (wei everywhere) for all onchain
  escrow deposits, task bounties, and performance stake locks. The Python
  market prices bounties in AXM and keeps its own payout path; the onchain
  mirror is the ETH-settled trustless alternative until AXM settlement lands.
- **Price discovery:** Trustless onchain Vickrey auction. Winner and
  clearing price are computed onchain by `vickreyResult(bytes32)` — a `view`
  function that reads state but mutates nothing — from revealed bids. The
  caller never supplies the winner.
- **Dual-pool re-auction credit invariant:**
  - **Pool 1 — offchain AXM credit ledger (Python engine):** Non-revealing
    (ghosting) bidders hold no ETH in `EscrowManager`. When `close_auction`
    fires offchain (no transaction), ghosting slashes populate the Python
    stake engine's internal `credits` dict via `credit_reauction()`
    (AXM-denominated).
  - **Pool 2 — onchain ETH credit mapping
    (`posterReAuctionBalances[poster]`):** Populated strictly by onchain
    `EscrowManager.adjudicate()` performance slashes (50% quality miss,
    100% hard abandonment). Backed 1:1 by ETH held in the escrow contract.
  - **Selection enforcement:** `selectWinnerAndFund(auctionId,
    creditToApply)` draws strictly from Pool 2
    (`posterReAuctionBalances[poster]`). Offchain AXM credits in Pool 1
    cannot offset onchain ETH funding requirements during selection.
- **Two-step funding sequence:** `selectWinnerAndFund` locks the poster's
  net ETH (+ `creditToApply`). The winning address posts required collateral
  via a separate `depositStake` call during `stakeDepositWindow`.

## Verbatim smart contract declarations

```solidity
// Structs & storage mappings
struct Commit {
    bytes32 commit;
    bool revealed;
    uint256 price;
}

mapping(bytes32 => mapping(address => Commit)) public commits;
mapping(address => uint256) public posterReAuctionBalances;

// Custom errors (IExecutionEscrowManager.sol & core)
error BadReveal();
error CommitWindowClosed();
error AlreadyCommitted();
error InsufficientReAuctionBalance(uint256 required, uint256 available);

// Core interface
function commit(bytes32 auctionId, bytes32 commitHash) external;

function reveal(
    bytes32 auctionId,
    uint256 price,
    bytes32 salt,
    bytes32 agentIdHash
) external;

function selectWinnerAndFund(
    bytes32 auctionId,
    uint96 creditToApply
) external payable;

function timeout(bytes32 auctionId) external;

function vickreyResult(bytes32 auctionId)
    public view returns (address winner, uint256 price);
```

## Commitment preimage & onchain verification

Client-side (Python, `sincor2.onchain.bidder_client.commitment`):

```python
price_bytes32 = price.to_bytes(32, byteorder="big")
commit_hash = keccak(auction_id + chain_id.to_bytes(32, "big")
                     + price_bytes32 + salt + agent_id_hash)
# == keccak256(abi.encodePacked(auctionId, block.chainid, bytes32(price),
#                               salt, agentIdHash))
# where agent_id_hash = keccak256(agent_id.encode("utf-8"))
#
# auctionId binds the commitment to ONE auction (W-4: cross-auction
# commitment replay); block.chainid binds it to ONE chain (cross-chain
# replay). The offchain sealed-bid shim omits both bindings
# (keccak(price‖salt‖agentIdHash)); shim hashes are NOT valid onchain
# commitments and must never be submitted to commit().
```

Onchain (`reveal`):

```solidity
bytes32 expected = keccak256(
    abi.encodePacked(auctionId, block.chainid, bytes32(price), salt, agentIdHash)
);

Commit storage entry = commits[auctionId][msg.sender];
if (expected != entry.commit) revert BadReveal();

entry.revealed = true;
entry.price = price;
```

Identity binding: the caller's address is mapped to the commitment in
contract state (`commits[auctionId][msg.sender]`); platform identity is
bound via `agentIdHash` inside the hash preimage; the auction id and the
chain id are likewise bound inside the preimage, so a commitment observed
on auction A cannot be revealed on auction B (W-4) or on another chain.
`agentIdHash` is verified at reveal but never stored — `vickreyResult`
returns the winner's *address*; the platform links it back to the agent
identity offchain.

## Execution architecture

```
[Agent / Offchain]              [Base L2 Auction Contract]      [Escrow Manager]
        |                                    |                       |
1. Verify unreserved stake                   |                       |
   (Python stake ledger)                     |                       |
        |                                    |                       |
2. commit() --------------------------> commit()                     |
                                        (writes entry.commit)        |
        |                                    |                       |
3. reveal() --------------------------> reveal()                     |
                                        (verifies expected ==        |
                                         entry.commit, sets          |
                                         revealed=true, price)       |
        |                                    |                       |
4. (ghosted / expired)                      |                       |
   anyone -> timeout() ----------------> timeout()                   |
   Python ledger slashes                  (finalized=true,           |
   non-revealer into Pool 1                emits TimedOut)           |
        |                                    |                       |
5. poster -> selectWinnerAndFund() ---> selectWinnerAndFund()        |
                                        (vickreyResult() ->         |
                                         (winner, price); draws     |
                                         creditToApply from Pool 2, |
                                         validates against          |
                                         posterReAuctionBalances    |
                                         [poster]) -----------------+--> poster ETH locked
        |                                                            |        |
6. winner -> depositStake() -----------------------------------------+--> winner collateral locked
   (inside stakeDepositWindow)                                       |        |
        |                                                            |        |
7. adjudication -----------------------------------------------------+--> adjudicate()
                                                                       (50% / 100% slash
                                                                        of winner stake
                                                                        -> Pool 2)
```

## Lifecycle phases

**Phase 1 — Pre-commit stake gate.** The Python stake ledger verifies the
bidder's unreserved collateral balance >= required threshold. Onchain
`commit()` does not inspect collateral.

**Phase 2 — Commit window (5 minutes).** Worker key calls
`commit(auctionId, commitHash)`. The contract checks timing windows,
asserts `commitHash != bytes32(0)` and
`commits[auctionId][msg.sender].commit == bytes32(0)`, then writes
`commits[auctionId][msg.sender].commit`.

**Phase 3 — Reveal window (5 minutes).** Worker key calls
`reveal(auctionId, price, salt, agentIdHash)`. The contract derives
`expected = keccak256(abi.encodePacked(auctionId, block.chainid,
bytes32(price), salt, agentIdHash))`, loads `Commit storage entry =
commits[auctionId][msg.sender]`, reverts `BadReveal()` on mismatch, then
sets `entry.revealed = true; entry.price = price;`. Prices are bounded to
`uint96.max` (a larger price would silently truncate in the escrow's
downcast and brick selection).

**Phase 4 — Circuit breaker & dual-ledger ghost slashing.** Anyone can call
`timeout(auctionId)` after the reveal deadline: sets `finalized = true`,
emits `TimedOut`, transfers nothing. Ghosting slash: non-revealers hold no
ETH in `EscrowManager`; when `close_auction` runs in the Python engine it
liquidates 100% of the non-revealer's offchain stake and invokes
`credit_reauction()`, crediting the poster's offchain AXM ledger (Pool 1).

**Phase 5 — Vickrey selection & onchain credit offset.** Poster calls
`selectWinnerAndFund{value: posterEth}(auctionId, creditToApply)`. The
contract calls `vickreyResult(auctionId)` to obtain `(address winner,
uint256 price)`. From the escrow's perspective (`msg.sender` is the auction
core), it validates `creditToApply <= posterReAuctionBalances[poster]`,
reverting `InsufficientReAuctionBalance(creditToApply, posterBalance)` on
overdraw; otherwise it decrements the poster's Pool 2 balance and locks net
poster ETH into escrow (`msg.value + creditToApply` must equal the Vickrey
price exactly). The winning address must then call
`depositStake{value: winnerStake}` within `stakeDepositWindow`.

**Phase 6 — SLA execution & adjudication (`EscrowManager`).**
`adjudicate()` resolves the winner's custodied stake: pass releases poster
ETH + 100% winner-stake return; 50% quality miss forfeits half the stake to
`posterReAuctionBalances[poster]` (Pool 2); 100% hard abandonment forfeits
all of it to Pool 2. Adjudication is currently single-key (adjudicator
address) — not decentralized.

## Verification checklist (eth-tester + pytest)

- [ ] Struct storage & type safety: `reveal()` reads
      `commits[auctionId][msg.sender]` as a `Commit` struct and writes
      `revealed = true`, `price`.
- [ ] Custom error parameters: overdrawn `creditToApply` reverts with
      `InsufficientReAuctionBalance(required, available)` per
      `IExecutionEscrowManager.sol`.
- [ ] Poster credit targeting: onchain credit checks target
      `posterReAuctionBalances[poster]` (the poster parameter passed by the
      auction core), not `msg.sender`.
- [ ] Two-pool credit separation: offchain ghosting slashes in
      `close_auction` write strictly to the Python AXM credit dict
      (Pool 1); they never touch `posterReAuctionBalances` (Pool 2).
- [ ] `vickreyResult` view contract: returns `(address winner, uint256
      price)` reading state, mutating nothing.
- [ ] Two-step escrow funding: escrow stays unactivated until the winning
      address executes `depositStake` within `stakeDepositWindow`.

## Cross-references

- Bidder wallet flow (external agents bidding from their own wallets):
  `docs/a2a/BIDDER_WALLET_FLOW.md`
- Reference bidder client:
  `src/sincor2/onchain/bidder_client.py`
- Deployment ceremony: `docs/deployment/AUCTION_DEPLOY_CEREMONY.md`
- Ratified auction-security decisions:
  `docs/ops/AUCTION_SECURITY_DECISIONS.md`
