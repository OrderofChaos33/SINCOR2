// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {CommitRevealAuction} from "../contracts/CommitRevealAuction.sol";
import {ExecutionEscrowManager} from "../contracts/ExecutionEscrowManager.sol";
import {IExecutionEscrowManager} from "../contracts/IExecutionEscrowManager.sol";
import {OptimisticBatchSettlement} from "../contracts/OptimisticBatchSettlement.sol";
import {StakeSlashManager} from "../contracts/StakeSlashManager.sol";

/**
 * @title Cross-contract funds-conservation invariant suite
 * @notice Execution Standard v2 (2026-11-04), blind-spot remediation #3.
 *         Before this file, the only on-chain invariant suite was
 *         SharedLiquidityVault (separate `onchain/` Foundry project) and
 *         NOTHING checked conservation across the money-path contracts in
 *         `contracts/`. The Python pool invariants cover the off-chain
 *         BountyPool ledger only; they cannot see these contracts.
 *
 * INVARIANTS (spec'd before the harness, per v2 directive section 1):
 *  I1 ETH conservation: ETH dealt to the actor set is at every step fully
 *     accounted for by actor balances + auction/escrow/batch balances.
 *     Nothing is created, nothing leaks to a non-actor address.
 *  I2 Escrow ledger solvency (exact): the escrow contract's ETH balance
 *     equals, to the wei, the sum of every obligation its ledgers record:
 *       sum over all escrows of (ethDeposited + creditBacking + agentStake)
 *       + bond of every active dispute
 *       + sum of posterReAuctionBalances (credit ledger, 1:1 ETH-backed)
 *       + sum of pendingWithdrawals (pull-payment ledger).
 *     In particular obligations NEVER exceed the balance.
 *  I3 Funding identity: every live escrow satisfies
 *     ethDeposited + creditBacking == bidAmount (exact two-sided funding).
 *  I4 Stake-token conservation: every minted collateral token sits in
 *     exactly one of {agents, StakeSlashManager, treasury}.
 *  I5 Stake ledger solvency (exact): the manager's token balance equals
 *     sum(stakeOf) + sum(posterReAuctionCredits) -- credits are ledger-only
 *     and 1:1 backed, the same pattern as I2.
 *  I6 OptimisticBatchSettlement holds no value: it has no payable or token
 *     surface (roots only), so its ETH balance must remain zero while the
 *     handler drives submit/challenge/finalize sequences.
 *
 * COMPOSITION BOUNDARIES (documented, not faked):
 *  - CommitRevealAuction + ExecutionEscrowManager compose natively over
 *    ETH (selectWinnerAndFund -> initializeEscrow) and are driven as one
 *    system by MoneyPathHandler.
 *  - StakeSlashManager is ERC20-collateralised and shares NO value path
 *    with the ETH auction/escrow pair (its poster credits are a separate
 *    ledger in a separate token; the contract comment itself defers the
 *    credit draw-down bridge). It is driven by its own handler and bound
 *    by I4/I5; system-wide conservation is I1 + I4 jointly.
 *  - OptimisticBatchSettlement moves no funds by design; I6 is the only
 *    conservation property it can have, and it is asserted while its
 *    state machine is exercised.
 *  - SharedLiquidityVault is NOT composed here: it lives in the separate
 *    onchain/ Foundry project (different src tree, solc pin and lib set,
 *    not vendored in this checkout), and no contract in contracts/
 *    references it. It already carries its own invariant suite there.
 */

contract XConservationToken is ERC20 {
    constructor() ERC20("XCONS", "XCONS") {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

/// @notice Drives the ETH money path: auction -> escrow lifecycle plus
///         batch-settlement root churn. All actions pre-check state and
///         return early instead of reverting (vault-handler style).
contract MoneyPathHandler is Test {
    CommitRevealAuction public auction;
    ExecutionEscrowManager public escrow;
    OptimisticBatchSettlement public batch;

    address public poster;
    address public challenger;
    address public adjudicator;
    address public batchOperator;
    address[] public workers;

    bytes32[] public auctionIds;
    // ghost commit data: the price/salt behind each on-chain commitment
    mapping(bytes32 => mapping(address => uint256)) public ghostPrice;
    mapping(bytes32 => mapping(address => bytes32)) public ghostSalt;

    uint256 public constant MAX_AUCTIONS = 12;

    constructor(
        CommitRevealAuction _auction,
        ExecutionEscrowManager _escrow,
        OptimisticBatchSettlement _batch,
        address _poster,
        address _challenger,
        address _adjudicator,
        address _batchOperator,
        address[] memory _workers
    ) {
        auction = _auction;
        escrow = _escrow;
        batch = _batch;
        poster = _poster;
        challenger = _challenger;
        adjudicator = _adjudicator;
        batchOperator = _batchOperator;
        for (uint256 i = 0; i < _workers.length; i++) workers.push(_workers[i]);
    }

    function auctionCount() external view returns (uint256) {
        return auctionIds.length;
    }

    function _pick(uint256 seed) internal view returns (bytes32 id, bool ok) {
        if (auctionIds.length == 0) return (bytes32(0), false);
        return (auctionIds[seed % auctionIds.length], true);
    }

    function _agentIdHash(address bidder) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked("agent", bidder));
    }

    // --- auction lifecycle -------------------------------------------------

    function openAuction(uint256 seed) external {
        if (auctionIds.length >= MAX_AUCTIONS) return;
        bytes32 id = keccak256(abi.encodePacked("xcons-auction", auctionIds.length, seed % 7));
        (bool opened,,,,) = auction.auctions(id);
        if (opened) return;
        vm.prank(poster);
        auction.openAuction(id, 120, 120);
        auctionIds.push(id);
    }

    function commitBid(uint256 seed, uint256 priceSeed) external {
        (bytes32 id, bool ok) = _pick(seed);
        if (!ok) return;
        (bool opened, bool finalized,, uint64 commitDeadline,) = auction.auctions(id);
        if (!opened || finalized || block.timestamp > commitDeadline) return;
        address bidder = workers[seed % workers.length];
        (bytes32 existing,,) = auction.commits(id, bidder);
        if (existing != bytes32(0)) return;
        uint256 price = bound(priceSeed, 0.05 ether, 2 ether);
        bytes32 salt = keccak256(abi.encodePacked("salt", id, bidder));
        ghostPrice[id][bidder] = price;
        ghostSalt[id][bidder] = salt;
        bytes32 commitHash =
            keccak256(abi.encodePacked(id, block.chainid, bytes32(price), salt, _agentIdHash(bidder)));
        vm.prank(bidder);
        auction.commit(id, commitHash);
    }

    function revealBid(uint256 seed) external {
        (bytes32 id, bool ok) = _pick(seed);
        if (!ok) return;
        (bool opened, bool finalized,, uint64 commitDeadline, uint64 revealDeadline) = auction.auctions(id);
        if (!opened || finalized) return;
        if (block.timestamp <= commitDeadline || block.timestamp > revealDeadline) return;
        address bidder = workers[seed % workers.length];
        (bytes32 existing, bool revealed,) = auction.commits(id, bidder);
        if (existing == bytes32(0) || revealed) return;
        vm.prank(bidder);
        auction.reveal(id, ghostPrice[id][bidder], ghostSalt[id][bidder], _agentIdHash(bidder));
    }

    function selectWinner(uint256 seed) external {
        (bytes32 id, bool ok) = _pick(seed);
        if (!ok) return;
        (bool opened, bool finalized, address aPoster,, uint64 revealDeadline) = auction.auctions(id);
        if (!opened || finalized || aPoster != poster) return;
        if (block.timestamp <= revealDeadline) return;
        uint256 price;
        try auction.vickreyResult(id) returns (address, uint256 p) {
            price = p;
        } catch {
            return; // no revealed bids
        }
        uint256 fund = escrow.posterReAuctionBalances(poster);
        uint256 maxCredit = price < fund ? price : fund;
        uint256 credit = bound(seed, 0, maxCredit);
        vm.prank(poster);
        auction.selectWinnerAndFund{value: price - credit}(id, uint96(credit));
    }

    function timeoutAuction(uint256 seed) external {
        (bytes32 id, bool ok) = _pick(seed);
        if (!ok) return;
        (bool opened, bool finalized,,, uint64 revealDeadline) = auction.auctions(id);
        if (!opened || finalized || block.timestamp <= revealDeadline) return;
        auction.timeout(id);
    }

    // --- escrow lifecycle --------------------------------------------------

    function depositStake(uint256 seed) external {
        (bytes32 id, bool ok) = _pick(seed);
        if (!ok) return;
        IExecutionEscrowManager.Escrow memory e = escrow.getEscrow(id);
        if (e.state != IExecutionEscrowManager.ExecutionState.AwaitingStake) return;
        if (block.timestamp > e.stakeDepositDeadline) return;
        uint256 required = (uint256(e.bidAmount) * escrow.minStakeBps()) / 10000;
        vm.prank(e.selectedAgent);
        escrow.depositStake{value: required}(id);
    }

    function submitResult(uint256 seed) external {
        (bytes32 id, bool ok) = _pick(seed);
        if (!ok) return;
        IExecutionEscrowManager.Escrow memory e = escrow.getEscrow(id);
        if (e.state != IExecutionEscrowManager.ExecutionState.ExecutionPhase) return;
        if (block.timestamp > e.executionDeadline) return;
        vm.prank(e.selectedAgent);
        escrow.submitResult(id, keccak256(abi.encodePacked("result", id)));
    }

    function openDispute(uint256 seed) external {
        (bytes32 id, bool ok) = _pick(seed);
        if (!ok) return;
        IExecutionEscrowManager.Escrow memory e = escrow.getEscrow(id);
        if (e.state != IExecutionEscrowManager.ExecutionState.DisputeWindow || e.disputeActive) return;
        if (block.timestamp > uint256(e.resultSubmittedTimestamp) + e.disputeWindowDuration) return;
        address disputer = seed % 2 == 0 ? poster : challenger;
        uint256 bond = escrow.challengerBond(); // read BEFORE prank: any call eats it
        vm.prank(disputer);
        escrow.openQualityDispute{value: bond}(id, bytes32(0));
    }

    function resolveDispute(uint256 seed) external {
        (bytes32 id, bool ok) = _pick(seed);
        if (!ok) return;
        IExecutionEscrowManager.Escrow memory e = escrow.getEscrow(id);
        if (e.state != IExecutionEscrowManager.ExecutionState.DisputeWindow || !e.disputeActive) return;
        vm.prank(adjudicator);
        escrow.resolveQualityDispute(id, seed % 2 == 0);
    }

    function rejectUnsubstantiated(uint256 seed) external {
        (bytes32 id, bool ok) = _pick(seed);
        if (!ok) return;
        IExecutionEscrowManager.Escrow memory e = escrow.getEscrow(id);
        if (e.state != IExecutionEscrowManager.ExecutionState.DisputeWindow || !e.disputeActive) return;
        IExecutionEscrowManager.Dispute memory d = escrow.getDispute(id);
        // handler only files zero-digest disputes, so the fast path applies
        // once the 6h evidence window has passed.
        if (block.timestamp <= uint256(d.disputeTimestamp) + 6 hours) return;
        escrow.rejectUnsubstantiatedDispute(id);
    }

    function timeoutEscrow(uint256 seed) external {
        (bytes32 id, bool ok) = _pick(seed);
        if (!ok) return;
        IExecutionEscrowManager.Escrow memory e = escrow.getEscrow(id);
        bool due;
        if (e.state == IExecutionEscrowManager.ExecutionState.AwaitingStake) {
            due = block.timestamp > e.stakeDepositDeadline;
        } else if (e.state == IExecutionEscrowManager.ExecutionState.ExecutionPhase) {
            due = block.timestamp > e.executionDeadline;
        } else if (e.state == IExecutionEscrowManager.ExecutionState.DisputeWindow && !e.disputeActive) {
            due = block.timestamp > uint256(e.resultSubmittedTimestamp) + e.disputeWindowDuration;
        } else if (e.state == IExecutionEscrowManager.ExecutionState.DisputeWindow && e.disputeActive) {
            IExecutionEscrowManager.Dispute memory d = escrow.getDispute(id);
            due = block.timestamp > uint256(d.disputeTimestamp) + e.adjudicationWindowDuration;
        }
        if (!due) return;
        escrow.timeout(id);
    }

    function withdrawPending(uint256 seed) external {
        address actor = seed % 3 == 0 ? poster : (seed % 3 == 1 ? challenger : workers[seed % workers.length]);
        if (escrow.pendingWithdrawals(actor) == 0) return;
        vm.prank(actor);
        escrow.withdraw();
    }

    // --- batch settlement (no-value surface) --------------------------------

    function batchSubmit(uint256 seed) external {
        bytes32 root = keccak256(abi.encodePacked("root", seed, batch.nextBatchId())); // before prank
        uint256 count = bound(seed, 1, 1000);
        vm.prank(batchOperator);
        batch.submitRoot(root, count);
    }

    function batchChallenge(uint256 seed) external {
        uint256 n = batch.nextBatchId();
        if (n == 0) return;
        uint256 batchId = seed % n;
        (bytes32 root,, uint256 submittedBlock, uint8 status,) = batch.batches(batchId);
        if (status != 0) return;
        if (block.number > submittedBlock + batch.CHALLENGE_BLOCKS()) return;
        bytes32 claimed = keccak256(abi.encodePacked("claimed", seed));
        if (claimed == root) return;
        // single-leaf tree: the root itself is the only leaf, empty proof
        batch.challenge(batchId, 0, root, claimed, new bytes32[](0), new uint8[](0));
    }

    function batchFinalize(uint256 seed) external {
        uint256 n = batch.nextBatchId();
        if (n == 0) return;
        uint256 batchId = seed % n;
        (,, uint256 submittedBlock, uint8 status,) = batch.batches(batchId);
        if (status != 0) return;
        if (block.number < submittedBlock + batch.CHALLENGE_BLOCKS()) return;
        batch.finalize(batchId);
    }

    // --- clocks --------------------------------------------------------------

    function advanceTime(uint256 seed) external {
        vm.warp(block.timestamp + bound(seed, 5, 600));
    }

    function advanceBlocks(uint256 seed) external {
        vm.roll(block.number + bound(seed, 1, 150));
    }
}

/// @notice Drives StakeSlashManager: stake / unstake / adjudicator-signed
///         slash sequences over a mock ERC20 collateral.
contract StakeConservationHandler is Test {
    StakeSlashManager public manager;
    XConservationToken public token;
    address public treasury;
    address[] public agents;
    address[] public creditPosters;
    uint256 public adjudicatorKey;
    uint256 public totalMinted;

    constructor(
        StakeSlashManager _manager,
        XConservationToken _token,
        address _treasury,
        address[] memory _agents,
        address[] memory _creditPosters,
        uint256 _adjudicatorKey,
        uint256 mintPerAgent
    ) {
        manager = _manager;
        token = _token;
        treasury = _treasury;
        adjudicatorKey = _adjudicatorKey;
        for (uint256 i = 0; i < _agents.length; i++) {
            agents.push(_agents[i]);
            token.mint(_agents[i], mintPerAgent);
            totalMinted += mintPerAgent;
            vm.prank(_agents[i]);
            token.approve(address(manager), type(uint256).max);
        }
        for (uint256 i = 0; i < _creditPosters.length; i++) creditPosters.push(_creditPosters[i]);
    }

    function agentCount() external view returns (uint256) {
        return agents.length;
    }

    function posterCount() external view returns (uint256) {
        return creditPosters.length;
    }

    function stake(uint256 seed, uint256 amt) external {
        address agent = agents[seed % agents.length];
        uint256 bal = token.balanceOf(agent);
        if (bal == 0) return;
        amt = bound(amt, 1, bal < 1000e18 ? bal : 1000e18);
        vm.prank(agent);
        manager.stake(amt);
    }

    function requestUnstake(uint256 seed, uint256 amt) external {
        address agent = agents[seed % agents.length];
        uint256 current = manager.stakeOf(agent);
        if (current == 0) return;
        amt = bound(amt, 1, current);
        // mirror the contract rule: a partial exit may not dip below
        // minStakeWei -- collapse to a full exit instead of reverting
        if (current - amt < manager.minStakeWei() && amt != current) amt = current;
        vm.prank(agent);
        manager.requestUnstake(amt);
    }

    function finalizeUnstake(uint256 seed) external {
        address agent = agents[seed % agents.length];
        if (manager.unstakePending(agent) == 0) return;
        if (block.timestamp < manager.unstakeReadyAt(agent)) return;
        vm.prank(agent);
        manager.finalizeUnstake();
    }

    function slash(uint256 seed, uint256 amtSeed, uint256 cutSeed) external {
        address agent = agents[seed % agents.length];
        uint256 current = manager.stakeOf(agent);
        if (current == 0) return;
        uint256 amount = bound(amtSeed, 1, current);
        uint256 cut = bound(cutSeed, 0, amount);
        address poster = creditPosters[seed % creditPosters.length];
        uint64 nonce = manager.slashNonceOf(agent);
        uint64 expiry = uint64(block.timestamp + 1 days);
        bytes32 reason = seed % 2 == 0 ? manager.REASON_GHOST() : manager.REASON_QUALITY();
        bytes32 structHash = keccak256(
            abi.encode(
                keccak256("SINCOR-SLASH"),
                block.chainid,
                address(manager),
                agent,
                poster,
                amount,
                cut,
                nonce,
                expiry,
                reason
            )
        );
        bytes32 ethSigned = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", structHash));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(adjudicatorKey, ethSigned);
        // pre-verify the signature recovers to the adjudicator (never revert)
        if (ecrecover(ethSigned, v, r, s) != manager.adjudicator()) return;
        manager.slash(agent, poster, amount, cut, nonce, expiry, reason, v, r, s);
    }

    function advanceTime(uint256 seed) external {
        vm.warp(block.timestamp + bound(seed, 5, 600));
    }
}

contract CrossContractConservationInvariantTest is Test {
    CommitRevealAuction auction;
    ExecutionEscrowManager escrow;
    OptimisticBatchSettlement batch;
    StakeSlashManager stakeManager;
    XConservationToken token;
    MoneyPathHandler moneyHandler;
    StakeConservationHandler stakeHandler;

    address constant POSTER = address(0xA001);
    address constant WORKER1 = address(0xA002);
    address constant WORKER2 = address(0xA003);
    address constant CHALLENGER = address(0xA004);
    address constant ADJUDICATOR = address(0xA005);
    address constant GUARDIAN = address(0xA006);
    address constant BATCH_OPERATOR = address(0xA007);
    address constant BATCH_TREASURY = address(0xA008);

    address constant STAKE_AGENT1 = address(0xB001);
    address constant STAKE_AGENT2 = address(0xB002);
    address constant STAKE_TREASURY = address(0xB004);
    address constant STAKE_ADMIN = address(0xB005);
    uint256 constant STAKE_ADJ_KEY = 0xAD;
    uint256 constant MINT_PER_AGENT = 100_000e18;

    uint256 constant POSTER_ETH = 10_000 ether;
    uint256 constant WORKER_ETH = 1_000 ether;
    uint256 constant CHALLENGER_ETH = 1_000 ether;
    uint256 constant TOTAL_ETH = POSTER_ETH + 2 * WORKER_ETH + CHALLENGER_ETH;

    function setUp() public {
        auction = new CommitRevealAuction(GUARDIAN);
        escrow = new ExecutionEscrowManager(address(auction), ADJUDICATOR, 2000, 0.01 ether, GUARDIAN);
        auction.setEscrowManager(address(escrow));
        // short execution-layer clocks so full lifecycles fit an invariant run
        auction.setExecutionParams(
            CommitRevealAuction.ExecutionParams({
                executionDuration: 300,
                disputeWindowDuration: 300,
                adjudicationWindowDuration: 300,
                stakeDepositWindow: 300
            })
        );
        batch = new OptimisticBatchSettlement(BATCH_TREASURY, BATCH_OPERATOR);

        token = new XConservationToken();
        stakeManager = new StakeSlashManager(
            STAKE_ADMIN, vm.addr(STAKE_ADJ_KEY), address(token), STAKE_TREASURY, 10e18, 120
        );

        vm.deal(POSTER, POSTER_ETH);
        vm.deal(WORKER1, WORKER_ETH);
        vm.deal(WORKER2, WORKER_ETH);
        vm.deal(CHALLENGER, CHALLENGER_ETH);

        address[] memory workers = new address[](2);
        workers[0] = WORKER1;
        workers[1] = WORKER2;
        moneyHandler = new MoneyPathHandler(
            auction, escrow, batch, POSTER, CHALLENGER, ADJUDICATOR, BATCH_OPERATOR, workers
        );

        address[] memory agents = new address[](2);
        agents[0] = STAKE_AGENT1;
        agents[1] = STAKE_AGENT2;
        address[] memory creditPosters = new address[](2);
        creditPosters[0] = POSTER;
        creditPosters[1] = CHALLENGER;
        stakeHandler = new StakeConservationHandler(
            stakeManager, token, STAKE_TREASURY, agents, creditPosters, STAKE_ADJ_KEY, MINT_PER_AGENT
        );

        targetContract(address(moneyHandler));
        targetContract(address(stakeHandler));
    }

    // I1: ETH conservation across the whole actor set + all contracts.
    function invariant_ethConservation() public view {
        uint256 sum = address(auction).balance + address(escrow).balance + address(batch).balance;
        sum += POSTER.balance + WORKER1.balance + WORKER2.balance + CHALLENGER.balance;
        assertEq(sum, TOTAL_ETH);
    }

    // I2: escrow obligations exactly equal the escrow's ETH balance.
    function invariant_escrowObligationsEqualBalance() public view {
        uint256 obligations;
        uint256 n = moneyHandler.auctionCount();
        for (uint256 i = 0; i < n; i++) {
            bytes32 id = moneyHandler.auctionIds(i);
            IExecutionEscrowManager.Escrow memory e = escrow.getEscrow(id);
            obligations += uint256(e.ethDeposited) + uint256(e.creditBacking) + uint256(e.agentStake);
            if (e.disputeActive) {
                obligations += uint256(escrow.getDispute(id).challengerBond);
            }
        }
        address[4] memory actors = [POSTER, WORKER1, WORKER2, CHALLENGER];
        for (uint256 i = 0; i < actors.length; i++) {
            obligations += escrow.posterReAuctionBalances(actors[i]);
            obligations += escrow.pendingWithdrawals(actors[i]);
        }
        assertEq(obligations, address(escrow).balance);
    }

    // I3: exact two-sided funding on every live escrow.
    function invariant_liveEscrowFundingIdentity() public view {
        uint256 n = moneyHandler.auctionCount();
        for (uint256 i = 0; i < n; i++) {
            IExecutionEscrowManager.Escrow memory e = escrow.getEscrow(moneyHandler.auctionIds(i));
            if (
                e.state == IExecutionEscrowManager.ExecutionState.AwaitingStake
                    || e.state == IExecutionEscrowManager.ExecutionState.ExecutionPhase
                    || e.state == IExecutionEscrowManager.ExecutionState.DisputeWindow
            ) {
                assertEq(uint256(e.ethDeposited) + uint256(e.creditBacking), uint256(e.bidAmount));
            }
        }
    }

    // I4: collateral token conservation.
    function invariant_stakeTokenConservation() public view {
        uint256 sum = token.balanceOf(address(stakeManager)) + token.balanceOf(STAKE_TREASURY);
        sum += token.balanceOf(STAKE_AGENT1) + token.balanceOf(STAKE_AGENT2);
        assertEq(sum, stakeHandler.totalMinted());
    }

    // I5: stake manager token balance exactly backs stakes + credits.
    function invariant_stakeObligationsEqualBalance() public view {
        uint256 obligations = stakeManager.stakeOf(STAKE_AGENT1) + stakeManager.stakeOf(STAKE_AGENT2);
        obligations += stakeManager.posterReAuctionCredits(POSTER);
        obligations += stakeManager.posterReAuctionCredits(CHALLENGER);
        assertEq(obligations, token.balanceOf(address(stakeManager)));
    }

    // I6: the batch settlement contract never holds value.
    function invariant_batchHoldsNoValue() public view {
        assertEq(address(batch).balance, 0);
    }
}

/// @notice Deterministic companion to the invariant campaign: drives the
///         SAME handlers through every lifecycle branch (dispute rejected,
///         bond forfeited, dispute upheld/slash, no-stake timeout, batch
///         challenge + finalize, stake slash + unstake) and asserts the end
///         states plus the conservation invariants. Random campaigns reach
///         the deep dispute branches rarely; this guarantees branch
///         coverage on every CI run.
contract CrossContractLifecycleTest is CrossContractConservationInvariantTest {
    // Track time in a local: the optimizer caches block.timestamp within
    // one function, so repeated vm.warp(block.timestamp + x) in a single
    // function would all compute from the same stale read. (Handler
    // advanceTime actions are separate calls and unaffected.)
    uint256 t;

    function _fwd(uint256 secs) internal {
        t += secs;
        vm.warp(t);
    }

    function _state(bytes32 id) internal view returns (IExecutionEscrowManager.ExecutionState) {
        return escrow.getEscrow(id).state;
    }

    function test_fullLifecyclesConserveFunds() public {
        t = block.timestamp;

        // Lifecycle A: poster self-dispute REJECTED -> worker paid in full.
        moneyHandler.openAuction(0);
        bytes32 a = moneyHandler.auctionIds(0);
        moneyHandler.commitBid(0, 1 ether);
        moneyHandler.commitBid(1, 1.5 ether);
        _fwd(121);
        moneyHandler.revealBid(0);
        moneyHandler.revealBid(1);
        _fwd(121);
        moneyHandler.selectWinner(0);
        assertEq(uint256(_state(a)), uint256(IExecutionEscrowManager.ExecutionState.AwaitingStake));
        moneyHandler.depositStake(0);
        assertEq(uint256(_state(a)), uint256(IExecutionEscrowManager.ExecutionState.ExecutionPhase));
        moneyHandler.submitResult(0);
        moneyHandler.openDispute(0); // poster disputes (even seed)
        assertTrue(escrow.getEscrow(a).disputeActive);
        moneyHandler.resolveDispute(1); // odd -> slashWorker=false -> reject
        assertEq(uint256(_state(a)), uint256(IExecutionEscrowManager.ExecutionState.Finalized));

        // Lifecycle B: third-party dispute REJECTED -> bond to poster fund.
        moneyHandler.openAuction(0);
        bytes32 b = moneyHandler.auctionIds(1);
        uint256 fundBefore = escrow.posterReAuctionBalances(POSTER);
        moneyHandler.commitBid(1, 1 ether); // id index 1, worker2
        moneyHandler.commitBid(3, 1.2 ether); // id index 1, worker1
        _fwd(121);
        moneyHandler.revealBid(1);
        moneyHandler.revealBid(3);
        _fwd(121);
        moneyHandler.selectWinner(1); // may apply accumulated credit
        assertEq(uint256(_state(b)), uint256(IExecutionEscrowManager.ExecutionState.AwaitingStake));
        moneyHandler.depositStake(1);
        moneyHandler.submitResult(1);
        moneyHandler.openDispute(1); // challenger disputes (odd seed)
        moneyHandler.resolveDispute(3); // index 1, odd -> reject
        assertEq(uint256(_state(b)), uint256(IExecutionEscrowManager.ExecutionState.Finalized));
        assertTrue(escrow.posterReAuctionBalances(POSTER) > fundBefore, "forfeited bond must credit poster fund");

        // Lifecycle C: dispute UPHELD -> 50% stake slashed to poster fund.
        moneyHandler.openAuction(0);
        bytes32 c = moneyHandler.auctionIds(2);
        uint256 fundBeforeC = escrow.posterReAuctionBalances(POSTER);
        moneyHandler.commitBid(2, 1 ether); // id index 2 (2 % 3), worker1
        moneyHandler.commitBid(5, 1.2 ether); // id index 2 (5 % 3), worker2
        _fwd(121);
        moneyHandler.revealBid(2);
        moneyHandler.revealBid(5);
        _fwd(121);
        moneyHandler.selectWinner(2);
        assertEq(uint256(_state(c)), uint256(IExecutionEscrowManager.ExecutionState.AwaitingStake));
        moneyHandler.depositStake(2);
        moneyHandler.submitResult(2);
        moneyHandler.openDispute(2); // poster disputes (even seed)
        moneyHandler.resolveDispute(8); // index 2 (8 % 3), even -> slash
        assertEq(uint256(_state(c)), uint256(IExecutionEscrowManager.ExecutionState.Finalized));
        assertTrue(escrow.posterReAuctionBalances(POSTER) > fundBeforeC, "slash must credit poster fund");

        // Lifecycle D: worker never stakes -> timeout refunds poster.
        moneyHandler.openAuction(0);
        bytes32 d = moneyHandler.auctionIds(3);
        moneyHandler.commitBid(3, 1 ether); // id index 3 (3 % 4), worker2
        _fwd(121);
        moneyHandler.revealBid(3);
        _fwd(121);
        moneyHandler.selectWinner(3);
        assertEq(uint256(_state(d)), uint256(IExecutionEscrowManager.ExecutionState.AwaitingStake));
        _fwd(301);
        moneyHandler.timeoutEscrow(3);
        assertEq(uint256(_state(d)), uint256(IExecutionEscrowManager.ExecutionState.TimedOut));

        // Batch settlement: challenge one root, finalize another.
        moneyHandler.batchSubmit(0);
        moneyHandler.batchSubmit(1);
        moneyHandler.batchChallenge(0);
        (,,, uint8 status0,) = batch.batches(0);
        assertEq(status0, 1);
        // Clear the batch challenge window before finalizing: since the
        // Base Denim block-window fix CHALLENGE_BLOCKS is 3000 (was 300),
        // and each advanceBlocks call rolls at most 150 blocks
        // (bound(seed, 1, 150)), loop until past the window: 21 x 150 =
        // 3150 >= 3000.
        for (uint256 i = 0; i < 21; i++) {
            moneyHandler.advanceBlocks(299);
        }
        moneyHandler.batchFinalize(1);
        (,,, uint8 status1,) = batch.batches(1);
        assertEq(status1, 2);

        // Stake manager: stake, adjudicator-signed slash, unstake round-trip.
        stakeHandler.stake(0, 500e18);
        uint256 staked = stakeManager.stakeOf(STAKE_AGENT1);
        assertTrue(staked > 0);
        stakeHandler.slash(0, 100e18, 40e18);
        assertTrue(stakeManager.stakeOf(STAKE_AGENT1) < staked, "slash must shrink stake");
        assertTrue(stakeManager.posterReAuctionCredits(POSTER) > 0, "slash must create credit");
        stakeHandler.stake(1, 500e18);
        uint256 agent2Stake = stakeManager.stakeOf(STAKE_AGENT2);
        assertTrue(agent2Stake > 0);
        stakeHandler.requestUnstake(1, agent2Stake); // full exit
        stakeHandler.advanceTime(599);
        uint256 balBefore = token.balanceOf(STAKE_AGENT2);
        stakeHandler.finalizeUnstake(1);
        assertEq(token.balanceOf(STAKE_AGENT2), balBefore + agent2Stake);
        assertEq(stakeManager.stakeOf(STAKE_AGENT2), 0);

        // Conservation invariants hold after the scripted storm.
        invariant_ethConservation();
        invariant_escrowObligationsEqualBalance();
        invariant_liveEscrowFundingIdentity();
        invariant_stakeObligationsEqualBalance();
        invariant_stakeTokenConservation();
        invariant_batchHoldsNoValue();
    }
}
