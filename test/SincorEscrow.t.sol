// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {SincorTaskEscrow} from "../contracts/escrow/SincorTaskEscrow.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract MockToken is ERC20 {
    constructor() ERC20("SINCOR", "SINCOR") {
        _mint(msg.sender, 1_000_000 * 1e18);
    }
}

contract SincorTaskEscrowTest is Test {
    SincorTaskEscrow public escrow;
    MockToken public token;

    address public creator = address(0x1);
    address public agent = address(0x2);
    address public treasury = address(0x3);
    address public reporter = address(0x4);

    uint256 constant REWARD = 100 * 1e18;
    uint256 constant BOND = 10 * 1e18;

    function setUp() public {
        token = new MockToken();
        escrow = new SincorTaskEscrow(address(token), treasury);

        token.transfer(creator, 10_000 * 1e18);
        token.transfer(agent, 10_000 * 1e18);

        vm.prank(creator);
        token.approve(address(escrow), type(uint256).max);

        vm.prank(agent);
        token.approve(address(escrow), type(uint256).max);
    }

    function _createAndClaim() internal returns (uint256 taskId) {
        vm.prank(creator);
        taskId = escrow.createTask(REWARD, BOND, 1 days);
        vm.prank(agent);
        escrow.claimTask(taskId);
    }

    function test_CreateAndClaimTask() public {
        vm.prank(creator);
        uint256 taskId = escrow.createTask(REWARD, BOND, 1 days);

        vm.prank(agent);
        escrow.claimTask(taskId);

        (,, uint256 reward, uint256 bond,, SincorTaskEscrow.TaskStatus status) = escrow.tasks(taskId);
        assertEq(reward, REWARD);
        assertEq(bond, BOND);
        assertEq(uint256(status), uint256(SincorTaskEscrow.TaskStatus.Claimed));
    }

    function test_ConstructorRevertsOnZeroAddress() public {
        vm.expectRevert("Invalid addresses");
        new SincorTaskEscrow(address(0), treasury);

        vm.expectRevert("Invalid addresses");
        new SincorTaskEscrow(address(token), address(0));
    }

    function test_CreateTaskRevertsOnZeroReward() public {
        vm.prank(creator);
        vm.expectRevert("Reward must be > 0");
        escrow.createTask(0, BOND, 1 days);
    }

    function test_CreateTaskPullsReward() public {
        uint256 beforeBal = token.balanceOf(creator);
        vm.prank(creator);
        escrow.createTask(REWARD, BOND, 1 days);
        assertEq(token.balanceOf(creator), beforeBal - REWARD);
        assertEq(token.balanceOf(address(escrow)), REWARD);
    }

    function test_ClaimPullsBond() public {
        vm.prank(creator);
        uint256 taskId = escrow.createTask(REWARD, BOND, 1 days);

        uint256 agentBefore = token.balanceOf(agent);
        vm.prank(agent);
        escrow.claimTask(taskId);
        assertEq(token.balanceOf(agent), agentBefore - BOND);
        assertEq(token.balanceOf(address(escrow)), REWARD + BOND);
    }

    function test_CannotDoubleClaim() public {
        uint256 taskId = _createAndClaim();
        vm.prank(address(0x99));
        vm.expectRevert("Task unavailable");
        escrow.claimTask(taskId);
    }

    function test_CannotClaimExpired() public {
        vm.prank(creator);
        uint256 taskId = escrow.createTask(REWARD, BOND, 1 days);

        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(agent);
        vm.expectRevert("Task expired");
        escrow.claimTask(taskId);
    }

    function test_CompletePaysAgentRewardAndBond() public {
        uint256 taskId = _createAndClaim();
        uint256 agentBefore = token.balanceOf(agent);

        vm.prank(creator);
        escrow.completeTask(taskId);

        assertEq(token.balanceOf(agent), agentBefore + REWARD + BOND);
        (,,,,, SincorTaskEscrow.TaskStatus status) = escrow.tasks(taskId);
        assertEq(uint256(status), uint256(SincorTaskEscrow.TaskStatus.Verified));
    }

    function test_CompleteRevertsIfNotCreator() public {
        uint256 taskId = _createAndClaim();
        vm.prank(agent);
        vm.expectRevert("Unauthorized verification");
        escrow.completeTask(taskId);
    }

    function test_SlashAfterExpirySplitsBond() public {
        uint256 taskId = _createAndClaim();
        uint256 creatorBefore = token.balanceOf(creator);

        vm.warp(block.timestamp + 1 days + 1);
        escrow.slashTask(taskId, reporter);

        assertEq(token.balanceOf(creator), creatorBefore + REWARD);
        assertEq(token.balanceOf(reporter), BOND / 2);
        assertEq(token.balanceOf(treasury), BOND - BOND / 2);

        (,,,,, SincorTaskEscrow.TaskStatus status) = escrow.tasks(taskId);
        assertEq(uint256(status), uint256(SincorTaskEscrow.TaskStatus.Slashed));
    }

    function test_CreatorCanSlashBeforeExpiry() public {
        uint256 taskId = _createAndClaim();
        vm.prank(creator);
        escrow.slashTask(taskId, reporter);
        (,,,,, SincorTaskEscrow.TaskStatus status) = escrow.tasks(taskId);
        assertEq(uint256(status), uint256(SincorTaskEscrow.TaskStatus.Slashed));
    }

    function test_NonCreatorCannotSlashBeforeExpiry() public {
        uint256 taskId = _createAndClaim();
        vm.prank(reporter);
        vm.expectRevert("Slash condition not met");
        escrow.slashTask(taskId, reporter);
    }

    function test_SlashRevertsOnZeroReporter() public {
        uint256 taskId = _createAndClaim();
        vm.prank(creator);
        vm.expectRevert("Invalid reporter");
        escrow.slashTask(taskId, address(0));
    }

    function test_ZeroBondClaimAndComplete() public {
        vm.prank(creator);
        uint256 taskId = escrow.createTask(REWARD, 0, 1 days);

        uint256 agentBefore = token.balanceOf(agent);
        vm.prank(agent);
        escrow.claimTask(taskId);
        assertEq(token.balanceOf(agent), agentBefore);

        vm.prank(creator);
        escrow.completeTask(taskId);
        assertEq(token.balanceOf(agent), agentBefore + REWARD);
    }
}
