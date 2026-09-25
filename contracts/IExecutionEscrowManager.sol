// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title IExecutionEscrowManager
 * @notice Interface for post-selection auction execution: escrow funding, result
 *         delivery, optimistic challenge clocks, slashing, and the poster
 *         re-auction fund.
 * @dev Composes downstream of CommitRevealAuction. The sealed-bid core owns
 *      price discovery (commit/reveal, winner selection); this module owns
 *      everything after agent selection. It never touches the commit phase.
 *
 *      Money model (two-sided funding):
 *        - The poster escrows the worker's payment (bidAmount) at initialization.
 *          The auction core forwards it: msg.value (fresh ETH) + creditToApply
 *          (drawn from the poster's re-auction fund) must equal bidAmount exactly.
 *        - The worker posts a stake >= minStakeBps of bidAmount via depositStake().
 *          Execution (and its deadline) starts only once the stake lands.
 *        - Slashed stake is real ETH that stays in this contract as the subsidy
 *          pool; posterReAuctionBalances is the ledger of who may draw it.
 *        - Credits are NEVER refunded as ETH. On poster refunds the credit
 *          portion returns to the fund ledger; only the poster's own ETH
 *          deposit leaves as ETH. This keeps the fund non-extractable.
 */
interface IExecutionEscrowManager {
    enum ExecutionState {
        Uninitialized,
        AwaitingStake, // payment escrowed, waiting on worker stake deposit
        ExecutionPhase,
        DisputeWindow,
        Finalized,
        Slashed,
        TimedOut
    }

    enum SlashReason {
        ExecutionGhosting, // 100% of stake: worker never submitted before deadline
        QualityMiss // 50% of stake: optimistic batch upheld a quality dispute
    }

    struct Escrow {
        address poster;
        address selectedAgent;
        uint96 bidAmount; // total worker payment = ethDeposited + creditBacking
        uint96 ethDeposited; // poster's own ETH deposited at init
        uint96 creditBacking; // bidAmount portion backed by the re-auction fund pool
        uint96 agentStake;
        uint32 stakeDepositDeadline;
        uint32 executionDuration;
        uint32 executionDeadline;
        uint32 disputeWindowDuration;
        uint32 adjudicationWindowDuration;
        uint32 resultSubmittedTimestamp;
        bytes32 resultHash;
        ExecutionState state;
        bool disputeActive;
    }

    struct Dispute {
        address challenger;
        uint96 challengerBond;
        bytes32 batchDigest;
        uint32 disputeTimestamp;
    }

    // --- Events ---

    event EscrowInitialized(
        bytes32 indexed auctionId,
        address indexed poster,
        address indexed selectedAgent,
        uint256 bidAmount,
        uint256 ethDeposited,
        uint256 creditApplied,
        uint32 stakeDepositDeadline
    );

    event StakeDeposited(
        bytes32 indexed auctionId,
        address indexed agent,
        uint256 stakeAmount,
        uint32 executionDeadline
    );

    event ResultSubmitted(
        bytes32 indexed auctionId,
        address indexed agent,
        bytes32 resultHash,
        uint32 disputeDeadline
    );

    event QualityDisputeOpened(
        bytes32 indexed auctionId,
        address indexed challenger,
        bytes32 batchDigest,
        uint256 bondAmount,
        uint32 resolutionDeadline
    );

    event QualityDisputeResolved(
        bytes32 indexed auctionId,
        bool indexed slashUpheld,
        uint256 slashedAmount
    );

    event SlashExecuted(
        bytes32 indexed auctionId,
        address indexed agent,
        address indexed poster,
        SlashReason reason,
        uint256 amountSlashed,
        uint256 amountToReAuctionFund
    );

    event EscrowFinalized(
        bytes32 indexed auctionId,
        address indexed agent,
        uint256 payoutAmount,
        uint256 stakeReturned
    );

    event EscrowTimedOut(
        bytes32 indexed auctionId,
        address indexed poster,
        uint256 ethRefunded
    );

    event ReAuctionFundCredited(
        bytes32 indexed auctionId,
        address indexed poster,
        uint256 amount
    );

    event ReAuctionFundDrawn(
        address indexed poster,
        bytes32 indexed auctionId,
        uint256 creditUsed
    );

    event AdjudicatorRotated(
        address indexed previousAdjudicator,
        address indexed newAdjudicator
    );

    event ChallengerBondUpdated(uint256 newBond);

    // --- Custom Errors ---

    error Unauthorized();
    error InvalidState();
    error ZeroBidAmount();
    error ExecutionWindowExpired();
    error StakeDepositExpired();
    error DisputeWindowExpired();
    error InvalidResultHash();
    error InsufficientStake(uint256 required, uint256 provided);
    error InsufficientBond(uint256 required, uint256 provided);
    error InsufficientReAuctionBalance(uint256 required, uint256 available);
    error BondExceedsRecordableLimit(uint256 max, uint256 provided);
    error FundingMismatch(uint256 expected, uint256 provided);
    error TransferFailed();
    error InvalidStakeBps();

    // --- State-Changing Functions ---

    /**
     * @notice Initializes escrow for a selected auction. Called ONLY by the
     *         auction core, which forwards the poster's payment escrow.
     * @dev msg.value (fresh poster ETH) + creditToApply (fund ledger draw) must
     *      equal bidAmount exactly. The drawn credit is backed by pool ETH
     *      already held by this contract, so the full bidAmount is always
     *      funded even when the poster's fresh deposit is partial.
     */
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
    ) external payable;

    /**
     * @notice Selected worker posts stake (>= minStakeBps of bidAmount).
     * @dev Starts the execution clock. Until this lands, the worker has no
     *      capital at risk, so a funding timeout refunds the poster with
     *      NO slash -- you cannot slash what was never staked.
     */
    function depositStake(bytes32 auctionId) external payable;

    /**
     * @notice Worker submits the execution payload hash, moving the escrow
     *         into the dispute window and starting the challenge clock.
     */
    function submitResult(bytes32 auctionId, bytes32 resultHash) external;

    /**
     * @notice Opens a quality dispute against a submitted result.
     * @dev The poster disputes for free (they are the harmed party). Any other
     *      challenger must post `challengerBond`, which is returned if the
     *      dispute is upheld and slashed to the poster's fund if rejected.
     */
    function openQualityDispute(bytes32 auctionId, bytes32 batchDigest) external payable;

    /**
     * @notice Adjudicator resolves an active dispute.
     * @param slashWorker True: 50% of stake -> poster fund, poster refunded,
     *        worker keeps remaining 50% of stake. False: worker paid in full,
     *        false challenger's bond -> poster fund.
     */
    function resolveQualityDispute(bytes32 auctionId, bool slashWorker) external;

    /**
     * @notice Permissionless liveness ticker. Exactly one branch fires:
     *         - AwaitingStake + stake deadline passed -> refund poster, no slash.
     *         - ExecutionPhase + execution deadline passed -> 100% slash (ghosting).
     *         - DisputeWindow + window passed + NO dispute -> optimistic payout to worker.
     *         - DisputeWindow + resolution deadline passed + dispute open ->
     *           payout to worker, challenger bond returned (adjudicator went dark).
     */
    function timeout(bytes32 auctionId) external;

    // --- Governance ---

    function setAdjudicator(address newAdjudicator) external;
    function setChallengerBond(uint256 newBond) external;

    // --- Views ---

    function getEscrow(bytes32 auctionId) external view returns (Escrow memory);
    function getDispute(bytes32 auctionId) external view returns (Dispute memory);
    function getPosterReAuctionBalance(address poster) external view returns (uint256);
}
