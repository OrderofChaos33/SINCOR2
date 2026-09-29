// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "./IExecutionEscrowManager.sol";
import "./security/ScopedPausable.sol";

/**
 * @title ExecutionEscrowManager
 * @notice Post-selection execution layer for A2A auctions. Owns escrow funding,
 *         result delivery, optimistic challenge clocks, slashing, and the
 *         poster re-auction fund. Composes downstream of CommitRevealAuction.
 *
 * @dev Security properties:
 *      - Checks-Effects-Interactions everywhere: every state transition and
 *        balance mutation happens BEFORE any external call.
 *      - Low-level .call for payouts (agents may be contract wallets; the
 *        2300-gas stipend of .transfer would strand them).
 *      - Fund credits are ledger-only and never leave as ETH: poster refunds
 *        return the credit portion to the fund, the ETH portion as ETH.
 *      - The dispute filing window and the adjudication window are separate
 *        clocks, so a permissionless timeout() can never steamroll an open
 *        dispute -- it can only fire after the resolution deadline passes,
 *        and then it defaults to the optimistic (worker-favor) outcome.
 *      - The poster bonds on disputes exactly like any challenger: a free
 *        poster dispute is a zero-cost delay attack on the worker's payout.
 *        Disputes filed with no evidence can be fast-rejected after a short
 *        evidence window instead of stalling through the full adjudication
 *        window. A rejected dispute forfeits the bond: from a third-party
 *        challenger it is slashed to the poster's re-auction fund, but when
 *        the challenger IS the poster the bond goes to the worker as
 *        compensation instead -- otherwise the "slash" would be a
 *        self-transfer back into the filer's own fund and grief would cost
 *        gas + temporary lockup only.
 */
contract ExecutionEscrowManager is IExecutionEscrowManager, ScopedPausable {
    /// @notice The sealed-bid core; the only caller allowed to initialize escrows.
    address public immutable auctionCore;
    /// @notice Optimistic batch adjudicator; resolves quality disputes.
    address public adjudicator;
    /// @notice Minimum worker stake as basis points of bidAmount (e.g. 2000 = 20%).
    uint256 public immutable minStakeBps;
    /// @notice Bond required of anyone opening a dispute -- the poster
    ///         included (P0/W-24: a free poster dispute is a zero-cost delay
    ///         attack on the worker's payout).
    uint256 public challengerBond;
    /// @notice Floor for challengerBond. The anti-grief property of the bond
    ///         is that stalling a worker's payout must cost real money: at a
    ///         zero bond the original P0/W-24 free-delay vuln comes back in a
    ///         single setChallengerBond(0) tx, so the adjudicator can never
    ///         take it below this. Set at 0.01 ETH -- half the 0.02 ETH
    ///         deployment calibration: still ~2 orders of magnitude above a
    ///         Base dispute-cycle's gas, so grief stays priced rather than
    ///         free, while the adjudicator keeps room to tune the bond down
    ///         for cheaper dispute classes (or up for hotter ones) without
    ///         bricking the anti-grief guarantee.
    uint256 public constant MIN_CHALLENGER_BOND = 0.01 ether;
    /// @notice Short window after a dispute is opened during which the
    ///         challenger is expected to have evidence attached to it. If a
    ///         dispute is still evidence-free when this window passes, anyone
    ///         may fast-reject it via rejectEvidenceFreeDispute() instead of
    ///         waiting out the full adjudication window -- a zero-evidence
    ///         dispute would otherwise stall the worker's payout for days.
    uint32 public constant DISPUTE_EVIDENCE_WINDOW = 6 hours;

    mapping(bytes32 => Escrow) public escrows;
    mapping(bytes32 => Dispute) public disputes;
    /// @notice Ledger of re-auction credits per poster. Backed 1:1 by ETH held
    ///        in this contract (slashed stakes + slashed challenger bonds).
    mapping(address => uint256) public posterReAuctionBalances;
    /// @notice Pull-payment ledger. ETH payouts use push-try/pull-fallback:
    ///        if a recipient's receive() reverts, the funds are queued here
    ///        instead of bricking escrow resolution. Anyone can pull their
    ///        own queued funds via withdraw(); queued funds never block
    ///        anyone else's resolution.
    mapping(address => uint256) public pendingWithdrawals;

    modifier onlyAuctionCore() {
        if (msg.sender != auctionCore) revert Unauthorized();
        _;
    }

    modifier onlyAdjudicator() {
        if (msg.sender != adjudicator) revert Unauthorized();
        _;
    }

    constructor(
        address _auctionCore,
        address _adjudicator,
        uint256 _minStakeBps,
        uint256 _challengerBond,
        address _guardian
    ) ScopedPausable(_guardian) {
        if (_auctionCore == address(0) || _adjudicator == address(0)) revert Unauthorized();
        if (_minStakeBps == 0 || _minStakeBps > 10_000) revert InvalidStakeBps();
        // The constructor takes the same floor as setChallengerBond: deploying
        // with a sub-floor bond would bake the free-delay vuln in from genesis.
        if (_challengerBond < MIN_CHALLENGER_BOND) revert ChallengerBondBelowFloor(MIN_CHALLENGER_BOND, _challengerBond);
        auctionCore = _auctionCore;
        adjudicator = _adjudicator;
        minStakeBps = _minStakeBps;
        challengerBond = _challengerBond;
    }

    /// @inheritdoc IExecutionEscrowManager
    function initializeEscrow(
        bytes32 auctionId,
        address poster,
        address selectedAgent,
        uint96 bidAmount,
        uint96 creditToApply,
        uint32 executionDuration,
        uint32 disputeWindowDuration,
        uint32 adjudicationWindowDuration,
        uint32 stakeDepositWindow
    ) external payable override onlyAuctionCore {
        if (escrows[auctionId].state != ExecutionState.Uninitialized) revert InvalidState();
        if (bidAmount == 0) revert ZeroBidAmount();
        if (poster == address(0) || selectedAgent == address(0)) revert Unauthorized();

        uint256 posterBalance = posterReAuctionBalances[poster];
        if (creditToApply > posterBalance) {
            revert InsufficientReAuctionBalance(creditToApply, posterBalance);
        }
        uint256 funded = msg.value + creditToApply;
        if (funded != bidAmount) revert FundingMismatch(bidAmount, funded);

        // Draw the credit BEFORE recording: the pool ETH backing it never moves,
        // it is simply re-earmarked from the poster's fund to this auction.
        posterReAuctionBalances[poster] = posterBalance - creditToApply;

        uint32 stakeDepositDeadline = uint32(block.timestamp + stakeDepositWindow);
        escrows[auctionId] = Escrow({
            poster: poster,
            selectedAgent: selectedAgent,
            bidAmount: bidAmount,
            ethDeposited: uint96(msg.value),
            creditBacking: creditToApply,
            agentStake: 0,
            stakeDepositDeadline: stakeDepositDeadline,
            executionDuration: executionDuration,
            executionDeadline: 0,
            disputeWindowDuration: disputeWindowDuration,
            adjudicationWindowDuration: adjudicationWindowDuration,
            resultSubmittedTimestamp: 0,
            resultHash: bytes32(0),
            state: ExecutionState.AwaitingStake,
            disputeActive: false
        });

        emit EscrowInitialized(
            auctionId, poster, selectedAgent, bidAmount, msg.value, creditToApply, stakeDepositDeadline
        );
        if (creditToApply > 0) emit ReAuctionFundDrawn(poster, auctionId, creditToApply);
    }

    /// @inheritdoc IExecutionEscrowManager
    /// @dev Pausable: stake deposits inject new capital. In-flight escrows
    ///      (submitResult, disputes, timeout, withdraw) are never paused.
    function depositStake(bytes32 auctionId) external payable override whenNotPaused {
        Escrow storage esc = escrows[auctionId];
        if (esc.state != ExecutionState.AwaitingStake) revert InvalidState();
        if (msg.sender != esc.selectedAgent) revert Unauthorized();
        if (block.timestamp > esc.stakeDepositDeadline) revert StakeDepositExpired();

        uint256 required = (uint256(esc.bidAmount) * minStakeBps) / 10000;
        // Exact stake: overpayment would silently inflate the slash basis,
        // so it reverts rather than being absorbed into agentStake.
        if (msg.value != required) revert InsufficientStake(required, msg.value);

        esc.agentStake = uint96(msg.value);
        esc.executionDeadline = uint32(block.timestamp + esc.executionDuration);
        esc.state = ExecutionState.ExecutionPhase;

        emit StakeDeposited(auctionId, msg.sender, msg.value, esc.executionDeadline);
    }

    /// @inheritdoc IExecutionEscrowManager
    function submitResult(bytes32 auctionId, bytes32 resultHash) external override {
        Escrow storage esc = escrows[auctionId];
        if (esc.state != ExecutionState.ExecutionPhase) revert InvalidState();
        if (msg.sender != esc.selectedAgent) revert Unauthorized();
        if (block.timestamp > esc.executionDeadline) revert ExecutionWindowExpired();
        if (resultHash == bytes32(0)) revert InvalidResultHash();

        esc.state = ExecutionState.DisputeWindow;
        esc.resultSubmittedTimestamp = uint32(block.timestamp);
        esc.resultHash = resultHash;

        uint32 disputeDeadline = uint32(block.timestamp + esc.disputeWindowDuration);
        emit ResultSubmitted(auctionId, msg.sender, resultHash, disputeDeadline);
    }

    /// @inheritdoc IExecutionEscrowManager
    function openQualityDispute(bytes32 auctionId, bytes32 batchDigest) external payable override {
        Escrow storage esc = escrows[auctionId];
        if (esc.state != ExecutionState.DisputeWindow) revert InvalidState();
        if (esc.disputeActive) revert InvalidState();
        if (block.timestamp > uint256(esc.resultSubmittedTimestamp) + esc.disputeWindowDuration) {
            revert DisputeWindowExpired();
        }

        // P0/W-24: EVERYONE bonds, the poster included. A zero-cost poster
        // dispute stalls the worker's payout through the whole adjudication
        // window for free (griefer's delay attack), so the poster posts the
        // same challengerBond with the same economics as the challenger path:
        // returned if the dispute is upheld or if the adjudicator goes dark,
        // forfeited if the dispute is rejected. Routing on reject depends on
        // who filed: a third-party challenger's bond is slashed to the
        // poster's re-auction fund; a poster who disputes their OWN auction
        // and loses forfeits the bond to the worker instead (A2: crediting
        // the filer's own fund would make the "slash" a self-transfer).
        if (msg.value < challengerBond) revert InsufficientBond(challengerBond, msg.value);
        uint96 bond = uint96(msg.value);

        esc.disputeActive = true;
        uint32 disputeTimestamp = uint32(block.timestamp);
        disputes[auctionId] = Dispute({
            challenger: msg.sender,
            challengerBond: bond,
            batchDigest: batchDigest,
            disputeTimestamp: disputeTimestamp
        });

        uint32 resolutionDeadline = uint32(block.timestamp + esc.adjudicationWindowDuration);
        emit QualityDisputeOpened(auctionId, msg.sender, batchDigest, bond, resolutionDeadline);
    }

    /// @inheritdoc IExecutionEscrowManager
    function resolveQualityDispute(bytes32 auctionId, bool slashWorker) external override onlyAdjudicator {
        Escrow storage esc = escrows[auctionId];
        if (esc.state != ExecutionState.DisputeWindow || !esc.disputeActive) revert InvalidState();

        if (slashWorker) {
            _resolveDisputeSlash(esc, auctionId);
        } else {
            _resolveDisputeReject(esc, auctionId);
        }
    }

    /// @inheritdoc IExecutionEscrowManager
    function rejectEvidenceFreeDispute(bytes32 auctionId) external override {
        Escrow storage esc = escrows[auctionId];
        if (esc.state != ExecutionState.DisputeWindow || !esc.disputeActive) revert InvalidState();
        Dispute memory d = disputes[auctionId];
        // A dispute with a batch digest already carries evidence attached;
        // the fast path is only for disputes that were filed with none.
        if (d.batchDigest != bytes32(0)) revert DisputeHasEvidence();
        if (block.timestamp <= uint256(d.disputeTimestamp) + DISPUTE_EVIDENCE_WINDOW) {
            revert EvidenceWindowOpen();
        }
        _resolveDisputeReject(esc, auctionId);
    }

    /**
     * @dev Dispute upheld: 50% of the worker's stake is slashed into the
     *      poster's re-auction fund, the poster's payment is refunded (ETH as
     *      ETH, credit back to the fund ledger), the honest challenger's bond
     *      is returned, and the worker keeps the unslashed half of their stake.
     */
    function _resolveDisputeSlash(Escrow storage esc, bytes32 auctionId) internal {
        // Memory copies collapse the payment fields into two locals, keeping
        // the codegen stack shallow. All state writes go via esc.
        Escrow memory m = esc;
        Dispute memory d = disputes[auctionId];
        uint256 slashAmount = m.agentStake / 2;
        uint256 stakeRefund = m.agentStake - slashAmount;

        // 100% of the slashed portion -> poster re-auction fund.
        // The ETH stays in the contract; only the ledger moves.
        posterReAuctionBalances[m.poster] += slashAmount;
        emit ReAuctionFundCredited(auctionId, m.poster, slashAmount);
        emit SlashExecuted(
            auctionId, m.selectedAgent, m.poster, SlashReason.QualityMiss, slashAmount, slashAmount
        );
        emit QualityDisputeResolved(auctionId, true, slashAmount);

        esc.state = ExecutionState.Finalized;
        esc.disputeActive = false;
        esc.agentStake = 0;
        esc.ethDeposited = 0;
        esc.creditBacking = 0;
        delete disputes[auctionId];
        // The credit backing is restored to the fund ledger, never paid as
        // ETH -- credits must not become extractable.
        posterReAuctionBalances[m.poster] += m.creditBacking;

        _payout(m.poster, m.ethDeposited);
        // Honest challenger gets their bond back; they were right.
        if (d.challengerBond > 0) _payout(d.challenger, d.challengerBond);
        // Worker keeps the unslashed half of their stake.
        if (stakeRefund > 0) _payout(m.selectedAgent, stakeRefund);
    }

    /**
     * @dev Dispute rejected: the worker did the job and is paid in full
     *      (bidAmount + stake). A false challenger's bond is forfeited as the
     *      anti-griefing penalty: from a third-party challenger it is slashed
     *      to the poster's re-auction fund; when the challenger IS the poster
     *      (a rejected self-dispute) it goes to the WORKER as compensation
     *      for the grief/hold on their payout, via the same _payout
     *      push-try/pull-fallback machinery. Routing it to the poster's own
     *      fund would be a self-transfer -- grief priced at gas only.
     */
    function _resolveDisputeReject(Escrow storage esc, bytes32 auctionId) internal {
        Escrow memory m = esc;
        Dispute memory d = disputes[auctionId];
        // The credit backing was earned: it formed part of this payout.
        uint256 payout = uint256(m.bidAmount) + m.agentStake;
        if (d.challengerBond > 0) {
            if (d.challenger == m.poster) {
                payout += d.challengerBond;
            } else {
                posterReAuctionBalances[m.poster] += d.challengerBond;
                emit ReAuctionFundCredited(auctionId, m.poster, d.challengerBond);
            }
        }
        emit QualityDisputeResolved(auctionId, false, 0);
        emit EscrowFinalized(auctionId, m.selectedAgent, m.bidAmount, m.agentStake);

        esc.state = ExecutionState.Finalized;
        esc.disputeActive = false;
        esc.agentStake = 0;
        esc.ethDeposited = 0;
        esc.creditBacking = 0;
        delete disputes[auctionId];

        _payout(m.selectedAgent, payout);
    }

    /// @inheritdoc IExecutionEscrowManager
    function timeout(bytes32 auctionId) external override {
        Escrow storage esc = escrows[auctionId];

        // 1. Worker never posted stake: nothing was ever at risk, so no slash.
        //    Refund the poster and free the auction.
        if (esc.state == ExecutionState.AwaitingStake) {
            if (block.timestamp <= esc.stakeDepositDeadline) revert InvalidState();
            Escrow memory m = esc;
            esc.state = ExecutionState.TimedOut;
            esc.ethDeposited = 0;
            esc.creditBacking = 0;
            posterReAuctionBalances[m.poster] += m.creditBacking;
            emit EscrowTimedOut(auctionId, m.poster, m.ethDeposited);
            _payout(m.poster, m.ethDeposited);
            return;
        }

        // 2. Execution ghosting: worker staked but never submitted -> 100% slash.
        if (esc.state == ExecutionState.ExecutionPhase) {
            if (block.timestamp <= esc.executionDeadline) revert InvalidState();
            Escrow memory m = esc;
            esc.state = ExecutionState.Slashed;
            esc.agentStake = 0;
            esc.ethDeposited = 0;
            esc.creditBacking = 0;
            posterReAuctionBalances[m.poster] += m.agentStake + m.creditBacking;
            emit ReAuctionFundCredited(auctionId, m.poster, m.agentStake);
            emit SlashExecuted(
                auctionId, m.selectedAgent, m.poster, SlashReason.ExecutionGhosting, m.agentStake, m.agentStake
            );
            _payout(m.poster, m.ethDeposited);
            return;
        }

        // 3. Dispute window expired with no dispute: optimistic default pays the worker.
        if (esc.state == ExecutionState.DisputeWindow && !esc.disputeActive) {
            if (block.timestamp <= uint256(esc.resultSubmittedTimestamp) + esc.disputeWindowDuration) {
                revert InvalidState();
            }
            _finalizePayout(esc, auctionId);
            return;
        }

        // 4. Dispute open but the adjudicator went dark past the resolution
        //    deadline: liveness over safety. Worker is paid (optimistic
        //    assumption), and the challenger gets their bond back -- the
        //    silence was not their fault.
        if (esc.state == ExecutionState.DisputeWindow && esc.disputeActive) {
            // Resolution deadline derives from the filing timestamp: the
            // filing window and the adjudication window are separate clocks,
            // so timeout() can never steamroll an open dispute.
            Dispute memory d = disputes[auctionId];
            uint256 resolutionDeadline = uint256(d.disputeTimestamp) + esc.adjudicationWindowDuration;
            if (block.timestamp <= resolutionDeadline) revert InvalidState();
            esc.disputeActive = false;
            delete disputes[auctionId];
            // State -> Finalized BEFORE any external call (reentrancy).
            _finalizePayout(esc, auctionId);
            if (d.challengerBond > 0) _payout(d.challenger, d.challengerBond);
            return;
        }

        revert InvalidState();
    }

    /// @inheritdoc IExecutionEscrowManager
    function setAdjudicator(address newAdjudicator) external override onlyAdjudicator {
        if (newAdjudicator == address(0)) revert Unauthorized();
        emit AdjudicatorRotated(adjudicator, newAdjudicator);
        adjudicator = newAdjudicator;
    }

    /// @inheritdoc IExecutionEscrowManager
    function setChallengerBond(uint256 newBond) external override onlyAdjudicator {
        // Floor first: a sub-floor bond restores the P0/W-24 free-delay vuln
        // (zero bond = zero-cost grief on the worker's payout) in one tx.
        if (newBond < MIN_CHALLENGER_BOND) revert ChallengerBondBelowFloor(MIN_CHALLENGER_BOND, newBond);
        // Bound the bond so the uint96 dispute-record cast can never truncate.
        if (newBond > type(uint96).max) revert BondExceedsRecordableLimit(type(uint96).max, newBond);
        challengerBond = newBond;
        emit ChallengerBondUpdated(newBond);
    }

    /// @inheritdoc IExecutionEscrowManager
    function getEscrow(bytes32 auctionId) external view override returns (Escrow memory) {
        return escrows[auctionId];
    }

    /// @inheritdoc IExecutionEscrowManager
    function getDispute(bytes32 auctionId) external view override returns (Dispute memory) {
        return disputes[auctionId];
    }

    /// @inheritdoc IExecutionEscrowManager
    function getPosterReAuctionBalance(address poster) external view override returns (uint256) {
        return posterReAuctionBalances[poster];
    }

    /**
     * @dev Releases the full worker payout (bidAmount + stake). The credit
     *      backing is consumed here: it subsidized this payment, so it does
     *      NOT return to the fund ledger. State is finalized BEFORE the
     *      external call.
     */
    function _finalizePayout(Escrow storage esc, bytes32 auctionId) internal {
        address agent = esc.selectedAgent;
        uint256 bidAmount = esc.bidAmount;
        uint256 stake = esc.agentStake;
        esc.state = ExecutionState.Finalized;
        esc.agentStake = 0;
        esc.ethDeposited = 0;
        esc.creditBacking = 0;
        emit EscrowFinalized(auctionId, agent, bidAmount, stake);
        _payout(agent, bidAmount + stake);
    }

    /**
     * @dev Push-try / pull-fallback payout. Low-level call instead of
     *      .transfer: agents may be contract wallets, and the 2300-gas
     *      stipend would strand their payouts. If the recipient's receive()
     *      reverts, the funds are queued in pendingWithdrawals instead of
     *      reverting the whole transaction -- a greedy recipient can never
     *      brick escrow resolution or lock anyone else's funds.
     */
    function _payout(address to, uint256 amount) internal {
        if (amount == 0) return;
        (bool success, ) = payable(to).call{value: amount}("");
        if (!success) {
            pendingWithdrawals[to] += amount;
            emit PaymentQueued(to, amount);
        }
    }

    /// @notice Pull any ETH queued for the caller after a failed push payout.
    /// @dev If the caller's receive() still reverts, this reverts and the
    ///      funds stay queued -- the caller's problem alone, never a blocker.
    function withdraw() external override {
        uint256 amount = pendingWithdrawals[msg.sender];
        if (amount == 0) revert NothingToWithdraw();
        pendingWithdrawals[msg.sender] = 0;
        (bool success, ) = payable(msg.sender).call{value: amount}("");
        if (!success) revert TransferFailed();
        emit Withdrawn(msg.sender, amount);
    }
}
