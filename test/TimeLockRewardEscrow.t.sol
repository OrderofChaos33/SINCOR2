// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {TimeLockRewardEscrow} from "../contracts/TimeLockRewardEscrow.sol";

/// @notice Plain ERC20 mock with supply tracking (no-burn assertions read it).
contract TlreMockToken {
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amt) external {
        totalSupply += amt;
        balanceOf[to] += amt;
    }

    function approve(address spender, uint256 amt) external returns (bool) {
        allowance[msg.sender][spender] = amt;
        return true;
    }

    function transfer(address to, uint256 amt) external returns (bool) {
        _move(msg.sender, to, amt);
        return true;
    }

    function transferFrom(address from, address to, uint256 amt) external returns (bool) {
        uint256 a = allowance[from][msg.sender];
        require(a >= amt, "allowance");
        if (a != type(uint256).max) allowance[from][msg.sender] = a - amt;
        _move(from, to, amt);
        return true;
    }

    function _move(address from, address to, uint256 amt) internal {
        require(balanceOf[from] >= amt, "balance");
        balanceOf[from] -= amt;
        balanceOf[to] += amt;
    }
}

/// @notice Token that tries to reenter the escrow during its transfer()
///         payout leg. The reentry target/args are armed by the test; the
///         inner call's success is recorded so the test can assert it failed
///         while the outer payout completed exactly once.
contract TlreReentrantToken {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    TimeLockRewardEscrow public target;
    uint256 public targetId;
    bool public armed;
    bool public reenterViaExit;
    bool public innerSucceeded;

    function mint(address to, uint256 amt) external {
        balanceOf[to] += amt;
    }

    function approve(address spender, uint256 amt) external returns (bool) {
        allowance[msg.sender][spender] = amt;
        return true;
    }

    function arm(TimeLockRewardEscrow _target, uint256 _id, bool _viaExit) external {
        target = _target;
        targetId = _id;
        reenterViaExit = _viaExit;
        armed = true;
    }

    function transfer(address to, uint256 amt) external returns (bool) {
        _move(msg.sender, to, amt);
        if (armed && msg.sender == address(target)) {
            armed = false; // one shot: the guard, not this flag, is what is tested
            // msg.sender for the inner call is THIS token contract, not the
            // beneficiary -- but the escrow checks state/guard before the
            // beneficiary check ordering matters: claim() settles state and
            // holds the reentrancy lock during the outer transfer, and the
            // allocation is already settled, so any reentry must fail.
            try target.claim(targetId) {
                innerSucceeded = true;
            } catch {
                innerSucceeded = false;
            }
            if (reenterViaExit) {
                try target.exitEarly(targetId) {
                    innerSucceeded = true;
                } catch {}
            }
        }
        return true;
    }

    function transferFrom(address from, address to, uint256 amt) external returns (bool) {
        uint256 a = allowance[from][msg.sender];
        require(a >= amt, "allowance");
        if (a != type(uint256).max) allowance[from][msg.sender] = a - amt;
        _move(from, to, amt);
        return true;
    }

    function _move(address from, address to, uint256 amt) internal {
        require(balanceOf[from] >= amt, "balance");
        balanceOf[from] -= amt;
        balanceOf[to] += amt;
    }
}

/**
 * @notice Path 6 suite for TimeLockRewardEscrow. Covers allocation funding,
 *         linear-vesting claim math at multiple timestamps, the early-exit
 *         penalty and its redirection to the reward pool, the no-burn /
 *         conservation invariant, beneficiary isolation and owner-only
 *         administration, reentrancy on both payout paths, and double-claim
 *         prevention.
 */
contract TimeLockRewardEscrowTest is Test {
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
        uint256 indexed allocationId, address indexed beneficiary, uint256 paidToBeneficiary, uint256 penaltyToPool
    );
    event RewardPoolUpdated(address indexed oldPool, address indexed newPool);

    uint256 internal constant T0 = 1_700_000_000;
    uint256 internal constant TOTAL = 1000 ether;
    uint64 internal constant CLIFF = 7 days;
    uint64 internal constant DURATION = 30 days; // spec 5.2 vested-30d default
    address internal constant DEAD = 0x000000000000000000000000000000000000dEaD;

    TlreMockToken public token;
    TimeLockRewardEscrow public escrow;

    address public owner = address(0x0B1E);
    address public beneficiary = address(0xBE11);
    address public beneficiary2 = address(0xBE12);
    address public pool = address(0x9001);
    address public pool2 = address(0x9002);
    address public stranger = address(0x57AA);

    function setUp() public {
        vm.warp(T0);
        token = new TlreMockToken();
        escrow = new TimeLockRewardEscrow(owner, address(token), pool);
        token.mint(owner, 1_000_000 ether);
        vm.prank(owner);
        token.approve(address(escrow), type(uint256).max);
    }

    function _create(
        address who,
        uint256 amount,
        uint64 cliff,
        uint64 duration,
        uint16 penaltyBps
    ) internal returns (uint256) {
        vm.prank(owner);
        return escrow.createAllocation(who, amount, cliff, duration, penaltyBps);
    }

    function _default() internal returns (uint256) {
        return _create(beneficiary, TOTAL, CLIFF, DURATION, escrow.DEFAULT_PENALTY_BPS());
    }

    /// @dev Conservation: escrow balance == unsettled remainder of the one
    ///      live allocation the helper tracks; supply never changes (no burn).
    function _assertConservation(uint256 id) internal view {
        TimeLockRewardEscrow.Allocation memory a = escrow.getAllocation(id);
        assertEq(token.balanceOf(address(escrow)), a.totalAmount - a.settledAmount, "escrow balance == unsettled");
        assertEq(token.balanceOf(address(0)), 0, "zero address never receives");
        assertEq(token.balanceOf(DEAD), 0, "dead address never receives");
    }

    // --- constructor / creation -------------------------------------------------

    function test_Constructor_ZeroAddressGuards() public {
        vm.expectRevert(TimeLockRewardEscrow.InvalidConfig.selector);
        new TimeLockRewardEscrow(address(0), address(token), pool);
        vm.expectRevert(TimeLockRewardEscrow.InvalidConfig.selector);
        new TimeLockRewardEscrow(owner, address(0), pool);
        // The pool in particular can never be a burn sink.
        vm.expectRevert(TimeLockRewardEscrow.InvalidConfig.selector);
        new TimeLockRewardEscrow(owner, address(token), address(0));
    }

    function test_CreateAllocation_FundsAtomicallyAndRecordsSchedule() public {
        uint256 ownerBefore = token.balanceOf(owner);
        vm.expectEmit(true, true, false, true);
        emit AllocationCreated(0, beneficiary, TOTAL, uint64(T0), uint64(T0 + CLIFF), uint64(T0 + DURATION), 5000);
        uint256 id = _default();

        assertEq(id, 0);
        assertEq(token.balanceOf(owner), ownerBefore - TOTAL, "pulled from funder");
        assertEq(token.balanceOf(address(escrow)), TOTAL, "held in escrow");
        TimeLockRewardEscrow.Allocation memory a = escrow.getAllocation(id);
        assertEq(a.beneficiary, beneficiary);
        assertEq(a.totalAmount, TOTAL);
        assertEq(a.settledAmount, 0);
        assertEq(a.startTime, T0);
        assertEq(a.cliffTime, T0 + CLIFF);
        assertEq(a.endTime, T0 + DURATION);
        assertEq(a.penaltyBps, 5000);
        assertFalse(a.closed);
        _assertConservation(id);
    }

    function test_CreateAllocation_OnlyOwner() public {
        vm.prank(stranger);
        vm.expectRevert(TimeLockRewardEscrow.Unauthorized.selector);
        escrow.createAllocation(beneficiary, TOTAL, CLIFF, DURATION, 5000);
        // Even the beneficiary cannot create their own allocation.
        vm.prank(beneficiary);
        vm.expectRevert(TimeLockRewardEscrow.Unauthorized.selector);
        escrow.createAllocation(beneficiary, TOTAL, CLIFF, DURATION, 5000);
    }

    function test_CreateAllocation_Validation() public {
        vm.startPrank(owner);
        vm.expectRevert(TimeLockRewardEscrow.InvalidConfig.selector);
        escrow.createAllocation(address(0), TOTAL, CLIFF, DURATION, 5000);
        vm.expectRevert(TimeLockRewardEscrow.InvalidAmount.selector);
        escrow.createAllocation(beneficiary, 0, CLIFF, DURATION, 5000);
        vm.expectRevert(TimeLockRewardEscrow.InvalidSchedule.selector);
        escrow.createAllocation(beneficiary, TOTAL, CLIFF, 0, 5000);
        vm.expectRevert(TimeLockRewardEscrow.InvalidSchedule.selector);
        escrow.createAllocation(beneficiary, TOTAL, DURATION + 1, DURATION, 5000);
        vm.expectRevert(TimeLockRewardEscrow.InvalidPenalty.selector);
        escrow.createAllocation(beneficiary, TOTAL, CLIFF, DURATION, 10_001);
        vm.stopPrank();
        assertEq(escrow.nextAllocationId(), 0, "no allocation leaked through");
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    function test_CreateAllocation_UnfundedReverts() public {
        // Revoke the funder's approval: creation must fail rather than
        // record an unfunded allocation.
        vm.prank(owner);
        token.approve(address(escrow), 0);
        vm.prank(owner);
        vm.expectRevert(TimeLockRewardEscrow.TransferFailed.selector);
        escrow.createAllocation(beneficiary, TOTAL, CLIFF, DURATION, 5000);
    }

    // --- vesting math ---------------------------------------------------------------

    function test_VestedAmount_AcrossTimestamps() public {
        uint256 id = _default();
        assertEq(escrow.vestedAmount(id, T0), 0, "nothing at start (pre-cliff)");
        assertEq(escrow.vestedAmount(id, T0 + CLIFF - 1), 0, "cliff gates to zero");
        // At the cliff the linear schedule (from start) is already 7/30 in.
        assertEq(escrow.vestedAmount(id, T0 + CLIFF), (TOTAL * 7 days) / 30 days, "linear at cliff");
        assertEq(escrow.vestedAmount(id, T0 + 15 days), TOTAL / 2, "half at midpoint");
        assertEq(escrow.vestedAmount(id, T0 + DURATION - 1), (TOTAL * (DURATION - 1)) / DURATION, "just before end");
        assertEq(escrow.vestedAmount(id, T0 + DURATION), TOTAL, "full at end");
        assertEq(escrow.vestedAmount(id, T0 + DURATION + 365 days), TOTAL, "capped after end");
    }

    function test_VestedAmount_ZeroCliffVestsFromStart() public {
        uint256 id = _create(beneficiary, TOTAL, 0, DURATION, 5000);
        assertEq(escrow.vestedAmount(id, T0), 0);
        assertEq(escrow.vestedAmount(id, T0 + 3 days), TOTAL / 10, "10% at day 3 of 30");
    }

    function test_VestedAmount_UnknownIdReverts() public {
        vm.expectRevert(TimeLockRewardEscrow.AllocationNotFound.selector);
        escrow.vestedAmount(99, T0);
        vm.expectRevert(TimeLockRewardEscrow.AllocationNotFound.selector);
        escrow.getAllocation(99);
    }

    // --- claim() -----------------------------------------------------------------------

    function test_Claim_BeforeCliffReverts() public {
        uint256 id = _default();
        vm.warp(T0 + CLIFF - 1);
        vm.prank(beneficiary);
        vm.expectRevert(TimeLockRewardEscrow.NothingClaimable.selector);
        escrow.claim(id);
    }

    function test_Claim_PartialThenRemainder_SumsToTotal_NoPenalty() public {
        uint256 id = _default();

        vm.warp(T0 + 15 days);
        vm.expectEmit(true, true, false, true);
        emit Claimed(id, beneficiary, TOTAL / 2);
        vm.prank(beneficiary);
        escrow.claim(id);
        assertEq(token.balanceOf(beneficiary), TOTAL / 2);
        assertEq(token.balanceOf(pool), 0, "claim() never touches the pool");
        _assertConservation(id);

        // Immediate second claim: nothing new has vested.
        vm.prank(beneficiary);
        vm.expectRevert(TimeLockRewardEscrow.NothingClaimable.selector);
        escrow.claim(id);

        vm.warp(T0 + DURATION);
        vm.prank(beneficiary);
        escrow.claim(id);
        assertEq(token.balanceOf(beneficiary), TOTAL, "full total claimed across both claims");
        assertEq(token.balanceOf(pool), 0);
        TimeLockRewardEscrow.Allocation memory a = escrow.getAllocation(id);
        assertTrue(a.closed, "fully settled allocation closes");
        _assertConservation(id);

        // Double-claim after close is impossible.
        vm.prank(beneficiary);
        vm.expectRevert(TimeLockRewardEscrow.AllocationClosed.selector);
        escrow.claim(id);
    }

    function test_Claim_OnlyBeneficiary() public {
        uint256 id = _default();
        vm.warp(T0 + 15 days);
        vm.prank(stranger);
        vm.expectRevert(TimeLockRewardEscrow.Unauthorized.selector);
        escrow.claim(id);
        // The owner/funder cannot claim a beneficiary's vested funds either.
        vm.prank(owner);
        vm.expectRevert(TimeLockRewardEscrow.Unauthorized.selector);
        escrow.claim(id);
        // Nor can a different beneficiary.
        vm.prank(beneficiary2);
        vm.expectRevert(TimeLockRewardEscrow.Unauthorized.selector);
        escrow.claim(id);
        assertEq(token.balanceOf(beneficiary), 0, "nothing moved");
        _assertConservation(id);
    }

    function test_ClaimableAmount_TracksVesting() public {
        uint256 id = _default();
        assertEq(escrow.claimableAmount(id), 0);
        vm.warp(T0 + 15 days);
        assertEq(escrow.claimableAmount(id), TOTAL / 2);
        vm.prank(beneficiary);
        escrow.claim(id);
        assertEq(escrow.claimableAmount(id), 0);
        vm.warp(T0 + DURATION);
        assertEq(escrow.claimableAmount(id), TOTAL / 2);
    }

    // --- exitEarly(): penalty + redirection ---------------------------------------------

    function test_EarlyExit_AtStart_ReproducesSpecInstantClaim() public {
        // Spec 5.2: instant claim = 50% paid, 50% redirected to the pool.
        uint256 id = _default();
        uint256 supplyBefore = token.totalSupply();

        vm.expectEmit(true, true, false, true);
        emit EarlyExit(id, beneficiary, TOTAL / 2, TOTAL / 2);
        vm.prank(beneficiary);
        escrow.exitEarly(id);

        assertEq(token.balanceOf(beneficiary), TOTAL / 2, "net 50% paid");
        assertEq(token.balanceOf(pool), TOTAL / 2, "penalty redirected to pool");
        assertEq(token.balanceOf(address(escrow)), 0, "escrow emptied");
        assertEq(token.totalSupply(), supplyBefore, "NO BURN: supply conserved");
        _assertConservation(id);

        // Closed: neither path can pay again.
        vm.prank(beneficiary);
        vm.expectRevert(TimeLockRewardEscrow.AllocationClosed.selector);
        escrow.exitEarly(id);
        vm.prank(beneficiary);
        vm.expectRevert(TimeLockRewardEscrow.AllocationClosed.selector);
        escrow.claim(id);
    }

    function test_EarlyExit_MidVesting_PenaltyOnlyOnUnvested() public {
        uint256 id = _default();
        vm.warp(T0 + 15 days); // 500 vested, 500 unvested; penalty = 50% of 500

        vm.prank(beneficiary);
        escrow.exitEarly(id);

        assertEq(token.balanceOf(beneficiary), 500 ether + 250 ether, "vested in full + unvested minus penalty");
        assertEq(token.balanceOf(pool), 250 ether, "penalty = 50% of unvested only");
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    function test_EarlyExit_AfterPartialClaim_PenaltyOnRemainingUnvested() public {
        uint256 id = _default();
        vm.warp(T0 + 15 days);
        vm.prank(beneficiary);
        escrow.claim(id); // settles 500 vested
        vm.warp(T0 + 20 days); // vested now 666.66..., unvested 333.33...

        uint256 vestedAtExit = escrow.vestedAmount(id, block.timestamp);
        uint256 expectedPenalty = ((TOTAL - vestedAtExit) * 5000) / 10_000;
        uint256 balBefore = token.balanceOf(beneficiary);
        vm.prank(beneficiary);
        escrow.exitEarly(id);

        assertEq(token.balanceOf(pool), expectedPenalty, "penalty on remaining unvested only");
        assertEq(
            token.balanceOf(beneficiary) - balBefore,
            (vestedAtExit - TOTAL / 2) + (TOTAL - vestedAtExit - expectedPenalty),
            "vested remainder in full + unvested minus penalty"
        );
        assertEq(token.balanceOf(beneficiary) + token.balanceOf(pool), TOTAL, "every wei accounted for");
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    function test_EarlyExit_AfterFullVesting_NoPenalty() public {
        uint256 id = _default();
        vm.warp(T0 + DURATION);
        vm.prank(beneficiary);
        escrow.exitEarly(id);
        assertEq(token.balanceOf(beneficiary), TOTAL);
        assertEq(token.balanceOf(pool), 0, "no unvested portion, no penalty");
    }

    function test_EarlyExit_CustomPenaltyRates() public {
        // 0% penalty allocation: exit pays everything.
        uint256 id0 = _create(beneficiary, TOTAL, CLIFF, DURATION, 0);
        vm.prank(beneficiary);
        escrow.exitEarly(id0);
        assertEq(token.balanceOf(beneficiary), TOTAL);
        assertEq(token.balanceOf(pool), 0);

        // 100% penalty allocation: instant exit pays 0, all unvested to pool.
        uint256 idFull = _create(beneficiary2, TOTAL, CLIFF, DURATION, 10_000);
        vm.prank(beneficiary2);
        escrow.exitEarly(idFull);
        assertEq(token.balanceOf(beneficiary2), 0);
        assertEq(token.balanceOf(pool), TOTAL);
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    function test_EarlyExit_OnlyBeneficiary() public {
        uint256 id = _default();
        vm.prank(stranger);
        vm.expectRevert(TimeLockRewardEscrow.Unauthorized.selector);
        escrow.exitEarly(id);
        vm.prank(owner);
        vm.expectRevert(TimeLockRewardEscrow.Unauthorized.selector);
        escrow.exitEarly(id);
        _assertConservation(id);
    }

    function testFuzz_EarlyExit_SplitAlwaysConserves(uint256 amount, uint256 elapsed, uint16 penaltyBps) public {
        amount = bound(amount, 1, 10_000 ether);
        elapsed = bound(elapsed, 0, DURATION);
        penaltyBps = uint16(bound(uint256(penaltyBps), 0, 10_000));
        uint256 id = _create(beneficiary, amount, 0, DURATION, penaltyBps);
        vm.warp(T0 + elapsed);

        uint256 supplyBefore = token.totalSupply();
        vm.prank(beneficiary);
        escrow.exitEarly(id);

        assertEq(token.balanceOf(beneficiary) + token.balanceOf(pool), amount, "paid + penalty == total");
        assertEq(token.balanceOf(address(escrow)), 0, "escrow emptied");
        assertEq(token.totalSupply(), supplyBefore, "supply conserved (no burn)");
        assertEq(token.balanceOf(address(0)), 0);
        assertEq(token.balanceOf(DEAD), 0);
    }

    // --- reward pool administration ---------------------------------------------------------

    function test_SetRewardPool_OwnerOnly_AndZeroRejected() public {
        vm.prank(stranger);
        vm.expectRevert(TimeLockRewardEscrow.Unauthorized.selector);
        escrow.setRewardPool(pool2);
        vm.prank(owner);
        vm.expectRevert(TimeLockRewardEscrow.InvalidConfig.selector);
        escrow.setRewardPool(address(0));
        assertEq(escrow.rewardPool(), pool, "unchanged after failed attempts");
    }

    function test_SetRewardPool_RotationAffectsFuturePenaltiesOnly() public {
        uint256 id1 = _default();
        vm.prank(beneficiary);
        escrow.exitEarly(id1); // penalty lands in the original pool
        assertEq(token.balanceOf(pool), TOTAL / 2);

        vm.expectEmit(true, true, false, true);
        emit RewardPoolUpdated(pool, pool2);
        vm.prank(owner);
        escrow.setRewardPool(pool2);

        uint256 id2 = _default();
        vm.prank(beneficiary);
        escrow.exitEarly(id2); // penalty lands in the NEW pool
        assertEq(token.balanceOf(pool), TOTAL / 2, "old pool untouched after rotation");
        assertEq(token.balanceOf(pool2), TOTAL / 2, "new pool receives new penalties");
    }

    // --- beneficiary isolation ------------------------------------------------------------------

    function test_BeneficiaryIsolation_TwoAllocationsIndependent() public {
        uint256 idA = _create(beneficiary, TOTAL, CLIFF, DURATION, 5000);
        uint256 idB = _create(beneficiary2, 500 ether, 0, 90 days, 5000);

        vm.warp(T0 + 15 days);
        vm.prank(beneficiary);
        escrow.claim(idA);
        assertEq(token.balanceOf(beneficiary), TOTAL / 2);
        assertEq(token.balanceOf(beneficiary2), 0, "B untouched by A's claim");
        uint256 amtB = 500 ether;
        uint256 expectedB = (amtB * 15 days) / 90 days; // 15 of 90 days vested
        assertEq(escrow.claimableAmount(idB), expectedB, "B vests on its own schedule");

        vm.prank(beneficiary2);
        escrow.exitEarly(idB);
        TimeLockRewardEscrow.Allocation memory a = escrow.getAllocation(idA);
        assertFalse(a.closed, "A still open after B exits");
        assertEq(a.settledAmount, TOTAL / 2);
        assertEq(
            token.balanceOf(address(escrow)),
            TOTAL - TOTAL / 2,
            "escrow holds exactly A's unsettled remainder"
        );
    }

    // --- reentrancy --------------------------------------------------------------------------------

    function test_Reentrancy_ClaimPathPaysExactlyOnce() public {
        TlreReentrantToken rToken = new TlreReentrantToken();
        TimeLockRewardEscrow rEscrow = new TimeLockRewardEscrow(owner, address(rToken), pool);
        rToken.mint(owner, 1_000_000 ether);
        vm.prank(owner);
        rToken.approve(address(rEscrow), type(uint256).max);
        vm.prank(owner);
        uint256 id = rEscrow.createAllocation(beneficiary, TOTAL, 0, DURATION, 5000);

        vm.warp(T0 + DURATION); // fully vested: claim pays TOTAL in one transfer
        rToken.arm(rEscrow, id, false);
        vm.prank(beneficiary);
        rEscrow.claim(id);

        assertFalse(rToken.innerSucceeded(), "reentrant claim reverted (guard + settled state)");
        assertEq(rToken.balanceOf(beneficiary), TOTAL, "paid exactly once");
        assertEq(rToken.balanceOf(address(rEscrow)), 0);
    }

    function test_Reentrancy_EarlyExitPathPaysExactlyOnce() public {
        TlreReentrantToken rToken = new TlreReentrantToken();
        TimeLockRewardEscrow rEscrow = new TimeLockRewardEscrow(owner, address(rToken), pool);
        rToken.mint(owner, 1_000_000 ether);
        vm.prank(owner);
        rToken.approve(address(rEscrow), type(uint256).max);
        vm.prank(owner);
        uint256 id = rEscrow.createAllocation(beneficiary, TOTAL, CLIFF, DURATION, 5000);

        // Instant exit: beneficiary leg fires first and tries to reenter
        // both claim() and exitEarly() mid-payout.
        rToken.arm(rEscrow, id, true);
        vm.prank(beneficiary);
        rEscrow.exitEarly(id);

        assertFalse(rToken.innerSucceeded(), "reentrant calls reverted");
        assertEq(rToken.balanceOf(beneficiary), TOTAL / 2, "beneficiary paid exactly once");
        // The pool leg uses the plain mock pool address; the reentrant token
        // pays it from the escrow balance like any transfer.
        assertEq(rToken.balanceOf(pool), TOTAL / 2, "penalty paid exactly once");
        assertEq(rToken.balanceOf(address(rEscrow)), 0);
    }
}
