// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {StakeSlashManager} from "../contracts/StakeSlashManager.sol";

/// @notice Minimal ERC20 mock (the SUT only needs transfer/transferFrom).
contract MockCollateral {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amt) external {
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

    function transferFrom(
        address from,
        address to,
        uint256 amt
    ) external returns (bool) {
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
 * @notice W-33 regression tests: finalizeUnstake must never brick when an
 *         adjudicator slash lands between requestUnstake and finalizeUnstake.
 *         It finalizes min(pending, available) and emits the ACTUAL amount.
 */
contract StakeSlashManagerUnstakeClampTest is Test {
    // Must match StakeSlashManager.Unstaked for expectEmit.
    event Unstaked(address indexed agent, uint256 amount);

    MockCollateral public token;
    StakeSlashManager public mgr;

    uint256 internal constant ADJ_PK = 0xA11CE;
    address public adjudicator;
    address public agent = address(0xA6E77);
    address public poster = address(0xB055);
    address public treasury = address(0x7EA5);

    uint256 internal constant STAKE = 1000 ether;
    uint256 internal constant MIN_STAKE = 100 ether;
    uint256 internal constant TIMELOCK = 7 days;

    bytes32 internal constant RULING_TYPEHASH = keccak256("SINCOR-SLASH");

    function setUp() public {
        token = new MockCollateral();
        adjudicator = vm.addr(ADJ_PK);
        mgr = new StakeSlashManager(
            address(this), // admin
            adjudicator,
            address(token),
            treasury,
            MIN_STAKE,
            TIMELOCK
        );

        token.mint(agent, 10_000 ether);
        vm.prank(agent);
        token.approve(address(mgr), type(uint256).max);
        vm.prank(agent);
        mgr.stake(STAKE);
        assertEq(mgr.stakeOf(agent), STAKE);
    }

    /// @dev Execute an adjudicator-signed slash ruling (permissionless caller).
    function _slash(
        address target,
        address _poster,
        uint256 amount,
        uint256 treasuryCut,
        bytes32 reason
    ) internal {
        uint64 nonce = mgr.slashNonceOf(target);
        uint64 expiry = uint64(block.timestamp + 1 days);
        bytes32 structHash = keccak256(
            abi.encode(
                RULING_TYPEHASH,
                block.chainid,
                address(mgr),
                target,
                _poster,
                amount,
                treasuryCut,
                nonce,
                expiry,
                reason
            )
        );
        bytes32 ethSigned = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", structHash)
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(ADJ_PK, ethSigned);
        mgr.slash(
            target,
            _poster,
            amount,
            treasuryCut,
            nonce,
            expiry,
            reason,
            v,
            r,
            s
        );
    }

    function _finalizeAsAgent() internal {
        vm.prank(agent);
        mgr.finalizeUnstake();
    }

    /// @dev Warp past the agent's unstake timelock. Targets are derived from
    ///      the contract's own unstakeReadyAt -- never from `block.timestamp`
    ///      arithmetic inside a warp argument (forge 1.8.3 + solc 0.8.27 +
    ///      via_ir evaluates TIMESTAMP stale there; observed 2026-10-06).
    function _warpPastTimelock() internal {
        vm.warp(mgr.unstakeReadyAt(agent) + 1);
    }

    // (b) Baseline: normal full unstake still works end-to-end.
    function test_NormalFullUnstakeWorks() public {
        vm.prank(agent);
        mgr.requestUnstake(STAKE);

        _warpPastTimelock();

        uint256 balBefore = token.balanceOf(agent);
        vm.expectEmit(true, false, false, true);
        emit Unstaked(agent, STAKE);
        _finalizeAsAgent();

        assertEq(token.balanceOf(agent) - balBefore, STAKE, "agent paid full stake");
        assertEq(mgr.stakeOf(agent), 0, "stake cleared");
        assertEq(mgr.unstakePending(agent), 0, "pending cleared");
        assertEq(mgr.unstakeReadyAt(agent), 0, "readyAt cleared");
    }

    // (a) Core W-33 case: slash lands mid-timelock; finalize clamps and
    // emits the ACTUAL amount, not the requested amount.
    function test_SlashBetweenRequestAndFinalize_ClampsAndEmitsActual() public {
        vm.prank(agent);
        mgr.requestUnstake(STAKE);
        assertEq(mgr.unstakePending(agent), STAKE);

        // Adjudicator slashes 400 during the timelock (staged-flow or not,
        // slash() takes effect immediately on a valid ruling).
        _slash(agent, poster, 400 ether, 0, mgr.REASON_QUALITY());
        assertEq(mgr.stakeOf(agent), 600 ether, "stake shrunk by slash");

        _warpPastTimelock();

        uint256 balBefore = token.balanceOf(agent);
        // The OLD code reverted InsufficientStake() here -- funds bricked.
        vm.expectEmit(true, false, false, true);
        emit Unstaked(agent, 600 ether); // actual, not the requested 1000
        _finalizeAsAgent();

        assertEq(
            token.balanceOf(agent) - balBefore,
            600 ether,
            "agent receives clamped remainder"
        );
        assertEq(mgr.stakeOf(agent), 0, "no dust left");
        assertEq(mgr.unstakePending(agent), 0, "pending cleared");
        assertEq(mgr.unstakeReadyAt(agent), 0, "readyAt cleared");

        // Nothing bricked: a repeat finalize fails cleanly (no pending),
        // it does not leave stuck funds.
        vm.prank(agent);
        vm.expectRevert(StakeSlashManager.NoPendingUnstake.selector);
        mgr.finalizeUnstake();
    }

    // (c) Slash shrinks the stake below the pending amount on a PARTIAL
    // unstake request: clamp applies the same way.
    function test_PartialUnstakeRequest_SlashBelowPending_Clamps() public {
        vm.prank(agent);
        mgr.requestUnstake(300 ether);

        _slash(agent, poster, 800 ether, 0, mgr.REASON_GHOST());
        assertEq(mgr.stakeOf(agent), 200 ether);

        _warpPastTimelock();

        uint256 balBefore = token.balanceOf(agent);
        vm.expectEmit(true, false, false, true);
        emit Unstaked(agent, 200 ether);
        _finalizeAsAgent();

        assertEq(token.balanceOf(agent) - balBefore, 200 ether);
        assertEq(mgr.stakeOf(agent), 0);
        assertEq(mgr.unstakePending(agent), 0);
    }

    // (c, extreme) Full slash to zero mid-timelock: finalize must still
    // succeed (paying zero) rather than brick the account's unstake state.
    function test_SlashToZero_FinalizeSucceedsNotBricked() public {
        vm.prank(agent);
        mgr.requestUnstake(STAKE);

        _slash(agent, poster, STAKE, 0, mgr.REASON_GHOST());
        assertEq(mgr.stakeOf(agent), 0);

        _warpPastTimelock();

        uint256 balBefore = token.balanceOf(agent);
        vm.expectEmit(true, false, false, true);
        emit Unstaked(agent, 0);
        _finalizeAsAgent();

        assertEq(token.balanceOf(agent), balBefore, "nothing to pay, nothing paid");
        assertEq(mgr.unstakePending(agent), 0, "pending cleared -- not bricked");
        assertEq(mgr.unstakeReadyAt(agent), 0, "readyAt cleared -- not bricked");

        // Account can re-stake and unstake normally afterwards: full lifecycle
        // recovery, proving no state is stuck.
        vm.prank(agent);
        mgr.stake(500 ether);
        vm.prank(agent);
        mgr.requestUnstake(500 ether);
        _warpPastTimelock();
        uint256 bal2 = token.balanceOf(agent);
        _finalizeAsAgent();
        assertEq(token.balanceOf(agent) - bal2, 500 ether, "post-recovery unstake works");
    }

    // (c) Slashed below minStakeWei: full exit must still be requestable and
    // finalizable (requestUnstake's minStake guard must not strand a
    // slash-shrunk account either).
    function test_SlashedBelowMinStake_FullExitStillPossible() public {
        _slash(agent, poster, 950 ether, 0, mgr.REASON_QUALITY());
        assertEq(mgr.stakeOf(agent), 50 ether);
        assertLt(mgr.stakeOf(agent), mgr.minStakeWei());

        vm.prank(agent);
        mgr.requestUnstake(50 ether); // full exit despite < minStake

        _warpPastTimelock();

        uint256 balBefore = token.balanceOf(agent);
        vm.expectEmit(true, false, false, true);
        emit Unstaked(agent, 50 ether);
        _finalizeAsAgent();

        assertEq(token.balanceOf(agent) - balBefore, 50 ether);
        assertEq(mgr.stakeOf(agent), 0);
        assertEq(mgr.unstakePending(agent), 0);
    }
}
