// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

// Also brings ReentrancyGuard into scope: it is vendored at the bottom of
// security/ScopedPausable.sol (see the note there), the same import pattern
// ExecutionEscrowManager uses, so this contract needs no lib/ remappings.
import "./security/ScopedPausable.sol";

/**
 * @title TimeLockRewardEscrow
 * @notice Time-locked vesting escrow for SINCOR campaign reward allocations
 *         (Path 6: TimeLockRewardEscrow Early-Exit Penalties). Holds ERC-20
 *         reward allocations (AXM on Base in production) under linear
 *         vesting schedules with a cliff; beneficiaries claim vested amounts
 *         as they unlock, or exit early and forfeit a penalty on the
 *         UNVESTED portion. The penalty is redirected to the reward pool --
 *         it is NEVER burned.
 *
 * @dev Relationship to the 101K AXM campaign (spec:
 *      goals/101k-axm-giveaway-campaign/files/CAMPAIGN-SPEC.md, section 5.2
 *      "Diamond Hands" booster; payout ledger template in
 *      QUEST-SETUP-PACK.md):
 *      - The campaign ledger computes each participant's final amount
 *        off-chain (base x multipliers, bounded by the tier-pool ceilings)
 *        and freezes/publishes the eligibility list before any payout batch.
 *        This contract is the on-chain money layer for the vested leg of
 *        that ledger: the funder creates one Allocation per frozen ledger
 *        row, funded atomically at creation.
 *      - Spec 5.2 defaults served directly: an allocation created at t0 with
 *        the default 50% (5000 bps) early-exit penalty reproduces the spec's
 *        "instant claim" exactly -- exit at t0 pays 50% to the beneficiary
 *        and redirects 50% to the reward pool. The 30-day / 90-day vested
 *        choices are expressed as the allocation's vestingDuration; the
 *        x1.5 / x2 boost is a ledger-side multiplier already included in the
 *        allocated amount (the contract neither mints nor scales rewards).
 *      - Tier 3 pays with no vesting by default (spec section 4); that is
 *        expressed by simply not creating an allocation for those rows.
 *
 *      Assumptions where the spec is silent (conservative standard chosen):
 *      - Vesting is LINEAR from startTime with a cliff gate: nothing is
 *        claimable before cliffTime; at/after the cliff the vested amount
 *        is total * elapsed / duration (the cliff does not create a step --
 *        it only gates the first claim). cliffDuration may be 0.
 *      - The early-exit penalty applies ONLY to the unvested remainder;
 *        vested-but-unclaimed amounts are always paid in full, in both
 *        claim() and exitEarly(). The penalty rate is fixed per allocation
 *        at creation (default DEFAULT_PENALTY_BPS = 50%) and can never be
 *        changed afterwards, by anyone.
 *      - One Allocation per ledger row; a beneficiary may hold many.
 *
 *      Security properties:
 *      - NO BURN, EVER: this contract contains no burn path and never sends
 *        tokens to the zero address or a dead address. Penalties move to
 *        `rewardPool` and stay in circulation (platform fee canon: no-burn
 *        is platform-wide; spec 5.2 default is redirect-to-pool).
 *      - Beneficiary isolation: only an allocation's beneficiary can claim
 *        or exit it; allocations are fully independent ledgers.
 *      - No early withdrawal except through the penalty path: before full
 *        vesting the only ways tokens leave are claim() of already-vested
 *        amounts and exitEarly() with the penalty on the unvested part.
 *      - The owner CANNOT seize funds: the owner's only powers are creating
 *        allocations (which PULLS the owner's own tokens in) and rotating
 *        `rewardPool`. There is no revoke, clawback, sweep, or pause, and
 *        no function moves a beneficiary's vested amount anywhere but to
 *        the beneficiary. Rotating the pool affects only FUTURE penalties.
 *      - Checks-Effects-Interactions everywhere, plus nonReentrant on both
 *        payout paths (defense-in-depth, mirroring ExecutionEscrowManager):
 *        allocation state is fully settled BEFORE any token transfer, so a
 *        reentrant or double claim finds nothing left to pay.
 *      - Conservation: at all times the contract's token balance equals the
 *        sum over open allocations of (totalAmount - settledAmount).
 *        Penalties leave to the pool in the same transaction they accrue;
 *        nothing is ever stranded or minted.
 *      - Deployment is founder-gated and this contract is NOT deployed: no
 *        address is registered in CANONICAL_ADDRESSES.md for it. Sepolia
 *        drill before mainnet, per house practice.
 */
contract TimeLockRewardEscrow is ReentrancyGuard {
    // --- constants ----------------------------------------------------------
    /// @notice Spec 5.2 instant-claim default: 50% of the unvested portion
    ///         is redirected to the reward pool on early exit.
    uint16 public constant DEFAULT_PENALTY_BPS = 5000;
    uint16 public constant MAX_BPS = 10_000;

    // --- errors -------------------------------------------------------------
    error Unauthorized();
    error InvalidConfig();
    error InvalidAmount();
    error InvalidSchedule();
    error InvalidPenalty();
    error AllocationNotFound();
    error AllocationClosed();
    error NothingClaimable();
    error TransferFailed();

    // --- events ---------------------------------------------------------------
    event AllocationCreated(
        uint256 indexed allocationId,
        address indexed beneficiary,
        uint256 totalAmount,
        uint64 startTime,
        uint64 cliffTime,
        uint64 endTime,
        uint16 penaltyBps
    );
    event Claimed(uint256 indexed allocationId, address indexed beneficiary, uint256 amount);
    event EarlyExit(
        uint256 indexed allocationId,
        address indexed beneficiary,
        uint256 paidToBeneficiary,
        uint256 penaltyToPool
    );
    event RewardPoolUpdated(address indexed oldPool, address indexed newPool);

    // --- types ----------------------------------------------------------------
    struct Allocation {
        address beneficiary;
        uint256 totalAmount;
        /// @dev Gross amount already settled (paid to beneficiary + penalty
        ///      redirected to pool). claim() settles vested amounts 1:1;
        ///      exitEarly() settles the remainder and closes the allocation.
        uint256 settledAmount;
        uint64 startTime;
        uint64 cliffTime;
        uint64 endTime;
        uint16 penaltyBps;
        bool closed;
    }

    // --- storage --------------------------------------------------------------
    /// @notice Funder/admin. Immutable: creates (and funds) allocations and
    ///         rotates the reward pool. Has NO power over allocated funds.
    address public immutable owner;
    /// @notice The reward token held in escrow (AXM in production).
    address public immutable rewardToken;
    /// @notice Destination for early-exit penalties. Never a burn address:
    ///         the constructor and the setter both reject the zero address,
    ///         and penalties are transferred here, never destroyed.
    address public rewardPool;

    uint256 public nextAllocationId;
    mapping(uint256 => Allocation) public allocations;

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    constructor(address _owner, address _rewardToken, address _rewardPool) {
        if (_owner == address(0) || _rewardToken == address(0) || _rewardPool == address(0)) {
            revert InvalidConfig();
        }
        owner = _owner;
        rewardToken = _rewardToken;
        rewardPool = _rewardPool;
    }

    // --- admin ------------------------------------------------------------------

    /// @notice Rotate the reward pool. Affects only penalties from exits
    ///         that happen after the rotation; settled penalties never move.
    function setRewardPool(address newPool) external onlyOwner {
        if (newPool == address(0)) revert InvalidConfig();
        emit RewardPoolUpdated(rewardPool, newPool);
        rewardPool = newPool;
    }

    /**
     * @notice Create and fund one vesting allocation. Pulls `totalAmount`
     *         of rewardToken from the owner in the same transaction, so an
     *         allocation can never exist unfunded.
     * @param beneficiary     Sole address allowed to claim/exit.
     * @param totalAmount     Gross allocation (ledger final amount, any
     *                        campaign multiplier already applied off-chain).
     * @param cliffDuration   Seconds from now until the cliff (0 allowed;
     *                        must be <= vestingDuration).
     * @param vestingDuration Seconds from now until fully vested (> 0).
     * @param penaltyBps      Early-exit penalty on the unvested portion, in
     *                        basis points (spec default DEFAULT_PENALTY_BPS).
     */
    function createAllocation(
        address beneficiary,
        uint256 totalAmount,
        uint64 cliffDuration,
        uint64 vestingDuration,
        uint16 penaltyBps
    ) external onlyOwner returns (uint256 allocationId) {
        if (beneficiary == address(0)) revert InvalidConfig();
        if (totalAmount == 0) revert InvalidAmount();
        if (vestingDuration == 0 || cliffDuration > vestingDuration) revert InvalidSchedule();
        if (penaltyBps > MAX_BPS) revert InvalidPenalty();

        allocationId = nextAllocationId++;
        uint64 start = uint64(block.timestamp);
        allocations[allocationId] = Allocation({
            beneficiary: beneficiary,
            totalAmount: totalAmount,
            settledAmount: 0,
            startTime: start,
            cliffTime: start + cliffDuration,
            endTime: start + vestingDuration,
            penaltyBps: penaltyBps,
            closed: false
        });
        emit AllocationCreated(
            allocationId, beneficiary, totalAmount, start, start + cliffDuration, start + vestingDuration, penaltyBps
        );
        _safeTransferFrom(msg.sender, address(this), totalAmount);
    }

    // --- beneficiary paths ------------------------------------------------------

    /// @notice Claim everything vested and not yet settled. No penalty:
    ///         vested amounts are the beneficiary's, in full.
    function claim(uint256 allocationId) external nonReentrant {
        Allocation storage a = _openAllocation(allocationId);
        if (msg.sender != a.beneficiary) revert Unauthorized();

        uint256 amount = _vestedAmount(a, block.timestamp) - a.settledAmount;
        if (amount == 0) revert NothingClaimable();

        // Effects before interactions: settle first, so a reentrant or
        // repeated claim computes zero.
        a.settledAmount += amount;
        if (a.settledAmount == a.totalAmount) a.closed = true;
        emit Claimed(allocationId, msg.sender, amount);

        _safeTransfer(msg.sender, amount);
    }

    /**
     * @notice Settle the allocation NOW and close it. The beneficiary
     *         receives all vested-but-unsettled amounts in full, plus the
     *         unvested remainder minus the penalty; the penalty is
     *         transferred to the reward pool in the same transaction.
     *         After endTime the unvested remainder is zero, so this is a
     *         penalty-free full claim.
     */
    function exitEarly(uint256 allocationId) external nonReentrant {
        Allocation storage a = _openAllocation(allocationId);
        if (msg.sender != a.beneficiary) revert Unauthorized();

        uint256 vested = _vestedAmount(a, block.timestamp);
        uint256 vestedDue = vested - a.settledAmount;
        uint256 unvested = a.totalAmount - vested;
        uint256 penalty = (unvested * a.penaltyBps) / MAX_BPS;
        uint256 paid = vestedDue + (unvested - penalty);

        // Effects before interactions: the allocation is fully settled and
        // closed before either transfer, so neither recipient can reenter
        // into a second payout.
        a.settledAmount = a.totalAmount;
        a.closed = true;
        emit EarlyExit(allocationId, msg.sender, paid, penalty);

        if (paid > 0) _safeTransfer(msg.sender, paid);
        if (penalty > 0) _safeTransfer(rewardPool, penalty);
    }

    // --- views --------------------------------------------------------------------

    function getAllocation(uint256 allocationId) external view returns (Allocation memory) {
        if (allocationId >= nextAllocationId) revert AllocationNotFound();
        return allocations[allocationId];
    }

    /// @notice Total amount vested at `timestamp` (0 before the cliff,
    ///         totalAmount at/after endTime, linear in between).
    function vestedAmount(uint256 allocationId, uint256 timestamp) public view returns (uint256) {
        if (allocationId >= nextAllocationId) revert AllocationNotFound();
        return _vestedAmount(allocations[allocationId], timestamp);
    }

    /// @notice Amount claim() would pay right now (vested minus settled).
    function claimableAmount(uint256 allocationId) external view returns (uint256) {
        if (allocationId >= nextAllocationId) revert AllocationNotFound();
        Allocation storage a = allocations[allocationId];
        if (a.closed) return 0;
        return _vestedAmount(a, block.timestamp) - a.settledAmount;
    }

    // --- internals ------------------------------------------------------------------

    function _openAllocation(uint256 allocationId) internal view returns (Allocation storage a) {
        if (allocationId >= nextAllocationId) revert AllocationNotFound();
        a = allocations[allocationId];
        if (a.closed) revert AllocationClosed();
    }

    function _vestedAmount(Allocation storage a, uint256 timestamp) internal view returns (uint256) {
        if (a.closed) return a.totalAmount;
        if (timestamp < a.cliffTime) return 0;
        if (timestamp >= a.endTime) return a.totalAmount;
        return (a.totalAmount * (timestamp - a.startTime)) / (a.endTime - a.startTime);
    }

    function _safeTransfer(address to, uint256 amount) internal {
        (bool ok, bytes memory ret) = rewardToken.call(
            abi.encodeWithSignature("transfer(address,uint256)", to, amount)
        );
        if (!ok || (ret.length > 0 && !abi.decode(ret, (bool)))) revert TransferFailed();
    }

    function _safeTransferFrom(address from, address to, uint256 amount) internal {
        (bool ok, bytes memory ret) = rewardToken.call(
            abi.encodeWithSignature("transferFrom(address,address,uint256)", from, to, amount)
        );
        if (!ok || (ret.length > 0 && !abi.decode(ret, (bool)))) revert TransferFailed();
    }
}
