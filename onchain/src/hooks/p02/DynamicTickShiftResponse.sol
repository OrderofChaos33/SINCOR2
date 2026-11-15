// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// @title DynamicTickShiftResponse
/// @notice p02 CLMM JIT defense — dynamic tick-shift response engine.
///
/// Task: activity-engine:p02 "Implement dynamic tick-shift response".
/// Spec: "On confirmed JIT detection, shift the managed position's ticks
/// away from the attack range by tick_distance steps, bounded by
/// vol_band, in afterModifyPosition. Includes cooldown to prevent
/// oscillation and a max-shift guard. Done = fork test shows managed
/// position migrates off the JIT range within one block; cooldown
/// prevents ping-pong across 100 simulated attacks."
///
/// Architecture: this contract is the response engine a Uniswap v4
/// hook (the p02 JITDefenseHook, extending LiquidityAmplifierHook)
/// delegates to from its afterModifyPosition callback. The hook owns
/// the PoolManager interaction; this engine owns the decision +
/// bookkeeping: whether a confirmed detection moves the managed
/// position, where to, and whether cooldown / vol-band / max-shift
/// guards veto the move. It is deliberately dependency-free (no v4
/// imports) so the response logic is testable in isolation — see
/// test/p02/DynamicTickShiftResponse.t.sol, which runs the acceptance
/// scenarios against a Base mainnet fork.
///
/// Safety properties:
///  - Only the authorized detector (the JIT detection module /
///    guardian) can confirm a detection; anyone else reverts.
///  - A shift is effective in the SAME block as the confirmation
///    (single call, no queue): the position range stored here is the
///    range the hook migrates to in that block.
///  - Cooldown: at most one shift per `cooldownBlocks` per pool, so
///    an attacker spraying detections cannot make the position
///    ping-pong; across N attack blocks the position moves at most
///    ceil(N / cooldownBlocks) times.
///  - Vol band: the shifted range is clamped inside
///    [volBandLower, volBandUpper]; if clamping would zero the shift,
///    the shift is refused rather than trapping the position.
///  - Max shift: cumulative displacement per pool is capped at
///    `maxTotalShift` ticks; beyond it the response refuses (the
///    position parks and the guardian is expected to intervene).
contract DynamicTickShiftResponse {
    /// @notice Result codes for a detection confirmation.
    uint8 public constant SHIFTED = 0;
    uint8 public constant NO_SHIFT_COOLDOWN = 1;
    uint8 public constant NO_SHIFT_NO_OVERLAP = 2;
    uint8 public constant NO_SHIFT_BAND = 3;
    uint8 public constant NO_SHIFT_MAX_SHIFT = 4;

    struct Position {
        int24 tickLower;
        int24 tickUpper;
        bool exists;
    }

    struct PoolState {
        Position position;
        int24 volBandLower;
        int24 volBandUpper;
        uint256 lastShiftBlock;
        bool hasShifted;
        int256 totalShift; // signed cumulative displacement (ticks)
        uint256 shiftCount;
    }

    /// @notice Address allowed to confirm JIT detections (the p02
    /// detection module / guardian).
    address public immutable detector;
    /// @notice Ticks the range moves per confirmed detection.
    int24 public immutable tickDistance;
    /// @notice Minimum blocks between two shifts on the same pool.
    uint256 public immutable cooldownBlocks;
    /// @notice Cap on cumulative |displacement| per pool, in ticks.
    int24 public immutable maxTotalShift;

    mapping(bytes32 => PoolState) internal _pools;

    event PositionRegistered(
        bytes32 indexed poolId, int24 tickLower, int24 tickUpper,
        int24 volBandLower, int24 volBandUpper
    );
    event PositionShifted(
        bytes32 indexed poolId, int24 oldLower, int24 oldUpper,
        int24 newLower, int24 newUpper, uint256 blockNumber
    );
    event ShiftRefused(bytes32 indexed poolId, uint8 reason, uint256 blockNumber);

    error OnlyDetector();
    error PositionMissing();
    error InvalidRange();

    constructor(
        address detector_,
        int24 tickDistance_,
        uint256 cooldownBlocks_,
        int24 maxTotalShift_
    ) {
        if (detector_ == address(0) || tickDistance_ <= 0 || maxTotalShift_ <= 0) {
            revert InvalidRange();
        }
        detector = detector_;
        tickDistance = tickDistance_;
        cooldownBlocks = cooldownBlocks_;
        maxTotalShift = maxTotalShift_;
    }

    /// @notice Register (or re-register) the managed position + vol
    /// band for a pool. Called by the hook when it opens the position.
    function registerPosition(
        bytes32 poolId,
        int24 tickLower,
        int24 tickUpper,
        int24 volBandLower,
        int24 volBandUpper
    ) external {
        if (tickLower >= tickUpper || volBandLower > tickLower
            || volBandUpper < tickUpper) {
            revert InvalidRange();
        }
        PoolState storage s = _pools[poolId];
        s.position = Position(tickLower, tickUpper, true);
        s.volBandLower = volBandLower;
        s.volBandUpper = volBandUpper;
        emit PositionRegistered(poolId, tickLower, tickUpper, volBandLower, volBandUpper);
    }

    /// @notice Production entry: confirm a JIT detection at the
    /// current block. The hook calls this from afterModifyPosition;
    /// a shift decided here is effective in this same block.
    function confirmJIT(bytes32 poolId, int24 attackLower, int24 attackUpper)
        external
        returns (uint8)
    {
        return _confirmJIT(poolId, attackLower, attackUpper, block.number);
    }

    /// @dev Core response logic, block-parameterized so fork tests can
    /// drive simulated block sequences through a harness subclass.
    function _confirmJIT(
        bytes32 poolId,
        int24 attackLower,
        int24 attackUpper,
        uint256 atBlock
    ) internal returns (uint8) {
        if (msg.sender != detector) revert OnlyDetector();
        PoolState storage s = _pools[poolId];
        if (!s.position.exists) revert PositionMissing();
        if (attackLower >= attackUpper) revert InvalidRange();

        int24 lower = s.position.tickLower;
        int24 upper = s.position.tickUpper;

        // No overlap with the attack range: nothing to flee.
        if (upper < attackLower || lower > attackUpper) {
            emit ShiftRefused(poolId, NO_SHIFT_NO_OVERLAP, atBlock);
            return NO_SHIFT_NO_OVERLAP;
        }
        // Cooldown: one shift per cooldownBlocks per pool. This is
        // the anti-ping-pong guard — detections inside the window are
        // acknowledged but do not move the position again.
        if (s.hasShifted && atBlock < s.lastShiftBlock + cooldownBlocks) {
            emit ShiftRefused(poolId, NO_SHIFT_COOLDOWN, atBlock);
            return NO_SHIFT_COOLDOWN;
        }

        // Direction: away from the attack center.
        int256 posCenter = (int256(lower) + int256(upper)) / 2;
        int256 attackCenter = (int256(attackLower) + int256(attackUpper)) / 2;
        int256 d = posCenter >= attackCenter
            ? int256(tickDistance)
            : -int256(tickDistance);

        // Max-shift guard: clamp the step to the remaining allowance.
        int256 newTotal = s.totalShift + d;
        if (newTotal > int256(maxTotalShift)) {
            d = int256(maxTotalShift) - s.totalShift;
        } else if (newTotal < -int256(maxTotalShift)) {
            d = -int256(maxTotalShift) - s.totalShift;
        }
        if (d == 0) {
            emit ShiftRefused(poolId, NO_SHIFT_MAX_SHIFT, atBlock);
            return NO_SHIFT_MAX_SHIFT;
        }

        // Vol-band clamp: the whole range must stay inside the band.
        int256 newLower = int256(lower) + d;
        int256 newUpper = int256(upper) + d;
        (newLower, newUpper) =
            _clampToBand(newLower, newUpper, s.volBandLower, s.volBandUpper);
        d = newLower - int256(lower);
        if (d == 0) {
            emit ShiftRefused(poolId, NO_SHIFT_BAND, atBlock);
            return NO_SHIFT_BAND;
        }

        s.position.tickLower = int24(newLower);
        s.position.tickUpper = int24(newUpper);
        s.totalShift += d;
        s.lastShiftBlock = atBlock;
        s.hasShifted = true;
        s.shiftCount += 1;
        emit PositionShifted(poolId, lower, upper, int24(newLower), int24(newUpper), atBlock);
        return SHIFTED;
    }

    /// @dev Clamp a shifted range inside the vol band, preserving
    /// range width (registration guarantees width <= band width).
    function _clampToBand(int256 newLower, int256 newUpper, int24 bandLower, int24 bandUpper)
        internal
        pure
        returns (int256, int256)
    {
        if (newLower < int256(bandLower)) {
            newUpper += int256(bandLower) - newLower;
            newLower = int256(bandLower);
        }
        if (newUpper > int256(bandUpper)) {
            newLower -= newUpper - int256(bandUpper);
            newUpper = int256(bandUpper);
        }
        return (newLower, newUpper);
    }

    // -- views ---------------------------------------------------------------

    function positionOf(bytes32 poolId)
        external
        view
        returns (int24 tickLower, int24 tickUpper, bool exists)
    {
        Position storage p = _pools[poolId].position;
        return (p.tickLower, p.tickUpper, p.exists);
    }

    function shiftStatsOf(bytes32 poolId)
        external
        view
        returns (uint256 lastShiftBlock, int256 totalShift, uint256 shiftCount)
    {
        PoolState storage s = _pools[poolId];
        return (s.lastShiftBlock, s.totalShift, s.shiftCount);
    }
}
