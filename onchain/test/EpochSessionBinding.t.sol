// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";

import {MockERC20} from "./mocks/MockERC20.sol";
import {EpochSessionKeyValidator} from "../src/sincor/EpochSessionKeyValidator.sol";
import {AgentStakeSlash} from "../src/sincor/AgentStakeSlash.sol";
import {MemoryBoundSettlement} from "../src/sincor/MemoryBoundSettlement.sol";

contract EpochSessionBindingTest is Test {
    MockERC20 internal token;
    EpochSessionKeyValidator internal validator;
    AgentStakeSlash internal stake;
    MemoryBoundSettlement internal settlement;

    address internal admin = address(this);
    address internal treasury = address(0xBEEF);
    address internal agent = address(0xA11CE);

    function setUp() external {
        token = new MockERC20("AXM", "AXM", 18);
        validator = new EpochSessionKeyValidator(admin);
        stake = new AgentStakeSlash(admin, token, treasury, 10e18);
        settlement = new MemoryBoundSettlement(admin, validator, stake, token);

        stake.grantRole(stake.SLASHER_ROLE(), address(settlement));
        token.mint(address(settlement), 1_000e18);
        token.mint(agent, 100e18);

        vm.prank(agent);
        token.approve(address(stake), type(uint256).max);
        vm.prank(agent);
        stake.stake(50e18);
    }

    function test_settleTask_requiresValidEpochProof() external {
        bytes32 epochId = keccak256("E1");
        bytes32 root = keccak256("ROOT");
        validator.publishEpochRoot(epochId, root);

        settlement.settleTask(keccak256("task-1"), agent, 5e18, epochId, root);
        assertEq(token.balanceOf(agent), 55e18);
    }

    function test_slashInvalidProof_burnsCollateralToTreasury() external {
        bytes32 epochId = keccak256("missing");
        bytes32 root = keccak256("bad");

        settlement.slashInvalidProof(keccak256("task-2"), agent, 10e18, epochId, root);
        assertEq(stake.stakeOf(agent), 40e18);
        assertEq(token.balanceOf(treasury), 10e18);
    }
}
