// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {DynamicTickShiftResponse} from "../../src/hooks/p02/DynamicTickShiftResponse.sol";

/// @notice Test harness: exposes the block-parameterized core so fork
/// tests can drive simulated block sequences (production confirmJIT
/// passes block.number; the harness passes explicit blocks). All
/// other logic is the production code path, unchanged.
contract TickShiftHarness is DynamicTickShiftResponse {
    constructor(address detector_, int24 tickDistance_, uint256 cooldownBlocks_, int24 maxTotalShift_)
        DynamicTickShiftResponse(detector_, tickDistance_, cooldownBlocks_, maxTotalShift_)
    {}

    function confirmJITAt(bytes32 poolId, int24 attackLower, int24 attackUpper, uint256 atBlock)
        external
        returns (uint8)
    {
        return _confirmJIT(poolId, attackLower, attackUpper, atBlock);
    }
}

/// @notice Acceptance tests for the p02 dynamic tick-shift response.
///
/// Run against a Base mainnet fork:
///   forge test --match-contract DynamicTickShiftResponseTest \
///       --fork-url https://mainnet.base.org
///
/// Acceptance criteria under test (from the task spec):
///  1. On confirmed JIT detection the managed position migrates off
///     the JIT range within one block.
///  2. Cooldown prevents ping-pong across 100 simulated attacks.
///  3. The shifted range stays bounded by the vol band.
///  4. The max-shift guard caps cumulative displacement.
///  5. Only the authorized detector can trigger a response.
///
/// No forge-std import: assertions are hand-rolled (require/revert)
/// so the suite runs in worktrees where lib/ is not vendored.
contract DynamicTickShiftResponseTest {
    bytes32 internal constant POOL = bytes32(uint256(902));

    function _engine(int24 tickDistance, uint256 cooldown, int24 maxShift)
        internal
        returns (DynamicTickShiftResponse)
    {
        // The test contract is the authorized detector.
        return new DynamicTickShiftResponse(address(this), tickDistance, cooldown, maxShift);
    }

    function _harness(int24 tickDistance, uint256 cooldown, int24 maxShift)
        internal
        returns (TickShiftHarness)
    {
        return new TickShiftHarness(address(this), tickDistance, cooldown, maxShift);
    }

    function _check(bool cond, string memory msg_) internal pure {
        require(cond, msg_);
    }

    function _overlaps(int24 l1, int24 u1, int24 l2, int24 u2) internal pure returns (bool) {
        return !(u1 < l2 || l1 > u2);
    }

    // 1. Migration off the attack range within one block (production
    //    entry point, real block.number — on the fork this is the
    //    fork block; the shift must be stamped with that same block).
    function test_migratesOffAttackRangeWithinOneBlock() public {
        DynamicTickShiftResponse e = _engine(200, 10, 5000);
        e.registerPosition(POOL, -100, 100, -2000, 2000);
        uint256 b = block.number;
        uint8 code = e.confirmJIT(POOL, -60, 60);
        _check(code == e.SHIFTED(), "expected SHIFTED");
        (int24 l, int24 u, bool exists) = e.positionOf(POOL);
        _check(exists, "position missing");
        _check(!_overlaps(l, u, -60, 60), "position still overlaps JIT range");
        (uint256 lastShift, , uint256 count) = e.shiftStatsOf(POOL);
        _check(lastShift == b, "shift not stamped with the confirmation block");
        _check(count == 1, "expected exactly one shift");
    }

    // 2. Cooldown prevents ping-pong across 100 simulated attacks.
    //    Attacks alternate sides every block (maximum oscillation
    //    pressure). With cooldownBlocks = 10 the position may move at
    //    most ceil(100/10) = 10 times, every realized shift must land
    //    outside the attack range that triggered it, and the range
    //    must stay inside the vol band throughout.
    function test_cooldownPreventsPingPongAcross100Attacks() public {
        TickShiftHarness h = _harness(200, 10, 100_000);
        h.registerPosition(POOL, 0, 200, -100_000, 100_000);
        uint256 shifts = 0;
        for (uint256 i = 1; i <= 100; i++) {
            (int24 l, int24 u, ) = h.positionOf(POOL);
            int24 center = (l + u) / 2;
            // Alternate: attack the left half, then the right half.
            int24 aL;
            int24 aU;
            if (i % 2 == 1) {
                aL = l - 50;
                aU = center;
            } else {
                aL = center;
                aU = u + 50;
            }
            uint8 code = h.confirmJITAt(POOL, aL, aU, i);
            if (code == h.SHIFTED()) {
                shifts++;
                (int24 nl, int24 nu, ) = h.positionOf(POOL);
                _check(!_overlaps(nl, nu, aL, aU), "shift landed inside the attack range");
                _check(nl >= -100_000 && nu <= 100_000, "shift escaped the vol band");
            } else {
                _check(code == h.NO_SHIFT_COOLDOWN(), "unexpected refusal reason");
            }
        }
        (, , uint256 count) = h.shiftStatsOf(POOL);
        _check(count == shifts, "shift count mismatch");
        _check(shifts <= 10, "cooldown failed: position ping-ponged");
        _check(shifts >= 1, "position never responded");
    }

    // 3. Vol-band clamp: a shift that would leave the band is clamped
    //    to the band edge, not refused outright and never out of band.
    function test_shiftClampedToVolBand() public {
        DynamicTickShiftResponse e = _engine(200, 10, 5000);
        e.registerPosition(POOL, 1800, 2000, -2000, 2100);
        uint8 code = e.confirmJIT(POOL, 1700, 1900);
        _check(code == e.SHIFTED(), "expected SHIFTED (clamped)");
        (int24 l, int24 u, ) = e.positionOf(POOL);
        _check(l == 1900 && u == 2100, "range not clamped to the band edge");
    }

    // 4. Max-shift guard: cumulative displacement is capped; once the
    //    cap is reached the response refuses with NO_SHIFT_MAX_SHIFT.
    function test_maxShiftGuardCapsDisplacement() public {
        TickShiftHarness h = _harness(200, 10, 300);
        h.registerPosition(POOL, 0, 200, -100_000, 100_000);
        // Attack the left half repeatedly (always pushes right).
        uint8 c1 = h.confirmJITAt(POOL, -50, 100, 1);
        _check(c1 == h.SHIFTED(), "first shift refused");
        uint8 c2 = h.confirmJITAt(POOL, 150, 300, 20); // left half of [200,400]
        _check(c2 == h.SHIFTED(), "second (clamped) shift refused");
        (, int256 total, ) = h.shiftStatsOf(POOL);
        _check(total == 300, "displacement not clamped at the cap");
        uint8 c3 = h.confirmJITAt(POOL, 250, 400, 40); // left half of [300,500]
        _check(c3 == h.NO_SHIFT_MAX_SHIFT(), "guard did not refuse past the cap");
    }

    // 5. Only the authorized detector can confirm a detection.
    function test_unauthorizedDetectorReverts() public {
        DynamicTickShiftResponse e =
            new DynamicTickShiftResponse(address(0xBEEF), 200, 10, 5000);
        e.registerPosition(POOL, -100, 100, -2000, 2000);
        bool reverted;
        try e.confirmJIT(POOL, -60, 60) returns (uint8) {
            reverted = false;
        } catch {
            reverted = true;
        }
        _check(reverted, "non-detector confirmation did not revert");
    }

    // 6. A detection that does not overlap the position is a no-op.
    function test_noOverlapNoShift() public {
        DynamicTickShiftResponse e = _engine(200, 10, 5000);
        e.registerPosition(POOL, -100, 100, -2000, 2000);
        uint8 code = e.confirmJIT(POOL, 500, 700);
        _check(code == e.NO_SHIFT_NO_OVERLAP(), "expected NO_SHIFT_NO_OVERLAP");
        (int24 l, int24 u, ) = e.positionOf(POOL);
        _check(l == -100 && u == 100, "position moved on a non-overlapping detection");
    }
}
