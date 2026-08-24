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

    function test_CreateAndClaimTask() public {
        vm.prank(creator);
        uint256 taskId = escrow.createTask(100 * 1e18, 10 * 1e18, 1 days);

        vm.prank(agent);
        escrow.claimTask(taskId);

        (,, uint256 reward, uint256 bond,, SincorTaskEscrow.TaskStatus status) = escrow.tasks(taskId);
        assertEq(reward, 100 * 1e18);
        assertEq(bond, 10 * 1e18);
        assertEq(uint256(status), 1); // Claimed
    }
}
