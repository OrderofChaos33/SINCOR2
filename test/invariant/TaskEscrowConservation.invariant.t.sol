// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {SincorTaskEscrow} from "../../contracts/escrow/SincorTaskEscrow.sol";

contract ConservationMockToken is ERC20 {
    constructor() ERC20("SINCOR", "SINCOR") {
        _mint(msg.sender, 1_000_000 * 1e18);
    }
}

/// @notice Handler driving SincorTaskEscrow through its full lifecycle
///         while tracking, in ghost state, exactly how much token value
///         the escrow should be holding:
///           createTask  -> +reward            (Unassigned holds reward)
///           claimTask   -> +bond              (Claimed holds reward+bond)
///           completeTask-> -(reward + bond)   (paid out to the agent)
///           slashTask   -> -(reward + bond)   (reward to creator,
///                                              bond split reporter/treasury)
///         Verified / Slashed tasks hold nothing.
contract TaskEscrowHandler is Test {
    SincorTaskEscrow public escrow;
    ConservationMockToken public token;
    address public creator;
    address public agent;
    address public reporter;

    uint256 public ghostHeld;
    uint256[] public taskIds;

    constructor(SincorTaskEscrow _escrow, ConservationMockToken _token, address _creator, address _agent, address _reporter) {
        escrow = _escrow;
        token = _token;
        creator = _creator;
        agent = _agent;
        reporter = _reporter;
        vm.prank(creator);
        token.approve(address(escrow), type(uint256).max);
        vm.prank(agent);
        token.approve(address(escrow), type(uint256).max);
    }

    function taskCount() external view returns (uint256) {
        return taskIds.length;
    }

    function create(uint256 reward, uint256 bond, uint256 duration) external {
        reward = bound(reward, 1, 100 ether);
        bond = bound(bond, 0, 50 ether);
        duration = bound(duration, 1 hours, 7 days);
        vm.prank(creator);
        uint256 id = escrow.createTask(reward, bond, duration);
        taskIds.push(id);
        ghostHeld += reward;
    }

    function claim(uint256 idx) external {
        if (taskIds.length == 0) return;
        uint256 id = taskIds[idx % taskIds.length];
        (, , , uint256 bond, uint256 expiry, SincorTaskEscrow.TaskStatus status) = escrow.tasks(id);
        if (status != SincorTaskEscrow.TaskStatus.Unassigned) return;
        if (block.timestamp >= expiry) return;
        vm.prank(agent);
        escrow.claimTask(id);
        ghostHeld += bond;
    }

    function complete(uint256 idx) external {
        if (taskIds.length == 0) return;
        uint256 id = taskIds[idx % taskIds.length];
        (, , uint256 reward, uint256 bond, , SincorTaskEscrow.TaskStatus status) = escrow.tasks(id);
        if (status != SincorTaskEscrow.TaskStatus.Claimed) return;
        vm.prank(creator);
        escrow.completeTask(id);
        ghostHeld -= reward + bond;
    }

    function slash(uint256 idx) external {
        if (taskIds.length == 0) return;
        uint256 id = taskIds[idx % taskIds.length];
        (, , uint256 reward, uint256 bond, , SincorTaskEscrow.TaskStatus status) = escrow.tasks(id);
        if (status != SincorTaskEscrow.TaskStatus.Claimed) return;
        // Creator as caller satisfies the slash condition at any time.
        vm.prank(creator);
        escrow.slashTask(id, reporter);
        ghostHeld -= reward + bond;
    }

    function warp(uint256 seconds_) external {
        vm.warp(block.timestamp + bound(seconds_, 1, 1 days));
    }
}

/// @title Cross-contract funds-conservation invariant — SincorTaskEscrow
/// @notice THE invariant: the escrow's token balance always equals the
///         sum of value the live task ledger says it holds —
///           sum over tasks of (Unassigned ? reward
///                              : Claimed   ? reward + bond
///                              : 0)
///         i.e. total escrowed == active bounties + posted bonds, with
///         every payout (completion) and every forfeit split (slash:
///         reward home, bond half to reporter / half to treasury)
///         leaving zero residual. Checked two independent ways: against
///         the handler's ghost accounting and by re-summing the task
///         ledger directly from contract state.
/// @dev    Mirrors the pattern of
///         onchain/test/invariant/SharedLiquidityVault.invariant.t.sol.
contract TaskEscrowConservationInvariant is Test {
    SincorTaskEscrow internal escrow;
    ConservationMockToken internal token;
    TaskEscrowHandler internal handler;

    address internal creator = address(0xC1EA7);
    address internal agent = address(0xA6E27);
    address internal treasury = address(0x7EA5);
    address internal reporter = address(0x1E90);

    function setUp() public {
        token = new ConservationMockToken();
        escrow = new SincorTaskEscrow(address(token), treasury);
        token.transfer(creator, 500_000 ether);
        token.transfer(agent, 500_000 ether);
        handler = new TaskEscrowHandler(escrow, token, creator, agent, reporter);
        targetContract(address(handler));
    }

    function _ledgerHeld() internal view returns (uint256 held) {
        uint256 n = handler.taskCount();
        for (uint256 i = 0; i < n; i++) {
            uint256 id = handler.taskIds(i);
            (, , uint256 reward, uint256 bond, , SincorTaskEscrow.TaskStatus status) = escrow.tasks(id);
            if (status == SincorTaskEscrow.TaskStatus.Unassigned) held += reward;
            else if (status == SincorTaskEscrow.TaskStatus.Claimed) held += reward + bond;
        }
    }

    function invariant_BalanceEqualsGhostHeld() public view {
        assertEq(token.balanceOf(address(escrow)), handler.ghostHeld());
    }

    function invariant_BalanceEqualsLedgerSum() public view {
        assertEq(token.balanceOf(address(escrow)), _ledgerHeld());
    }
}

/// @notice Deterministic lifecycle conservation checks (same identity,
///         no fuzzer): a mixed population of tasks in every state must
///         leave the escrow holding exactly the live tasks' value.
contract TaskEscrowConservationTest is Test {
    SincorTaskEscrow internal escrow;
    ConservationMockToken internal token;

    address internal creator = address(0xC1EA7);
    address internal agent = address(0xA6E27);
    address internal treasury = address(0x7EA5);
    address internal reporter = address(0x1E90);

    function setUp() public {
        token = new ConservationMockToken();
        escrow = new SincorTaskEscrow(address(token), treasury);
        token.transfer(creator, 10_000 ether);
        token.transfer(agent, 10_000 ether);
        vm.prank(creator);
        token.approve(address(escrow), type(uint256).max);
        vm.prank(agent);
        token.approve(address(escrow), type(uint256).max);
    }

    function _create(uint256 reward, uint256 bond) internal returns (uint256) {
        vm.prank(creator);
        return escrow.createTask(reward, bond, 1 days);
    }

    function test_ConservationAcrossMixedLifecycle() public {
        uint256 t0 = _create(100 ether, 10 ether); // stays Unassigned: holds 100
        uint256 t1 = _create(200 ether, 20 ether); // Claimed: holds 220
        uint256 t2 = _create(300 ether, 30 ether); // Completed: holds 0
        uint256 t3 = _create(400 ether, 40 ether); // Slashed: holds 0

        vm.prank(agent);
        escrow.claimTask(t1);
        vm.prank(agent);
        escrow.claimTask(t2);
        vm.prank(agent);
        escrow.claimTask(t3);

        vm.prank(creator);
        escrow.completeTask(t2);
        vm.prank(creator);
        escrow.slashTask(t3, reporter);

        assertEq(token.balanceOf(address(escrow)), 100 ether + 220 ether);
        // Slash split check: reporter got half the bond, treasury the rest.
        assertEq(token.balanceOf(reporter), 20 ether);
        assertEq(token.balanceOf(treasury), 20 ether);
        // Completion paid reward + bond back to the agent for t2.
        assertEq(token.balanceOf(agent), 10_000 ether - 20 ether - 30 ether - 40 ether + 330 ether);
        // Creator recovered the slashed task's reward.
        assertEq(token.balanceOf(creator), 10_000 ether - 1_000 ether + 400 ether);
    }

    function test_ConservationZeroBondTasks() public {
        uint256 t0 = _create(50 ether, 0);
        vm.prank(agent);
        escrow.claimTask(t0);
        assertEq(token.balanceOf(address(escrow)), 50 ether);
        vm.prank(creator);
        escrow.completeTask(t0);
        assertEq(token.balanceOf(address(escrow)), 0);
    }
}
