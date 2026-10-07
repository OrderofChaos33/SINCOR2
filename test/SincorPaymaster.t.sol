// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {
    SincorPaymaster,
    PackedUserOperation,
    IEntryPoint
} from "../contracts/paymaster/SincorPaymaster.sol";

/// @notice Minimal EntryPoint stand-in: tracks per-account deposits in wei
///         and honors withdrawTo, so withdraw flows are tested end-to-end.
contract MockEntryPoint {
    mapping(address => uint256) public deposits;

    function balanceOf(address account) external view returns (uint256) {
        return deposits[account];
    }

    function depositTo(address account) external payable {
        deposits[account] += msg.value;
    }

    function withdrawTo(address payable to, uint256 amount) external {
        require(deposits[msg.sender] >= amount, "insufficient deposit");
        deposits[msg.sender] -= amount;
        (bool ok, ) = to.call{value: amount}("");
        require(ok, "eth transfer failed");
    }

    receive() external payable {}
}

contract SincorPaymasterTest is Test {
    SincorPaymaster public pm;
    MockEntryPoint public ep;

    address public owner = address(this);
    address public agent = address(0xA6E17);
    address public attacker = address(0xBAD);
    address public withdrawer = address(0xD4A0);
    address public recipient = address(0x9EC1);

    event WithdrawerSet(address indexed withdrawer);
    event Withdrawn(address indexed to, uint256 amount);

    // Canonical ERC-4337 v0.7 IPaymaster selector for
    // validatePaymasterUserOp(PackedUserOperation,bytes32,uint256).
    bytes4 constant V07_SELECTOR = 0x52b7512c;

    function setUp() public {
        ep = new MockEntryPoint();
        pm = new SincorPaymaster(IEntryPoint(address(ep)));
        vm.deal(address(this), 100 ether);
        vm.deal(agent, 1 ether);
    }

    function _userOp(address sender)
        internal
        pure
        returns (PackedUserOperation memory uo)
    {
        uo.sender = sender;
        uo.nonce = 1;
        uo.initCode = hex"beef";
        uo.callData = hex"dead";
        uo.accountGasLimits = bytes32(uint256(100_000));
        uo.preVerificationGas = 50_000;
        uo.gasFees = bytes32(uint256(1 gwei));
        uo.paymasterAndData = hex"cafe";
        uo.signature = hex"f00d";
    }

    function _asEntryPointValidate(address sender, uint256 maxCost)
        internal
        returns (bytes memory ctx, uint256 vd)
    {
        vm.prank(address(ep));
        return pm.validatePaymasterUserOp(_userOp(sender), bytes32(uint256(0xdead)), maxCost);
    }

    function _openSponsorship() internal {
        pm.setProbation(agent, true);
        pm.setMaxSponsoredWei(10 ether);
    }

    // ── (a) selector matches the ERC-4337 EntryPoint expectation ──────────

    function test_SelectorMatchesV07EntryPoint() public pure {
        bytes4 sel = SincorPaymaster.validatePaymasterUserOp.selector;
        bytes4 expected = bytes4(
            keccak256(
                "validatePaymasterUserOp((address,uint256,bytes,bytes,bytes32,uint256,bytes32,bytes,bytes),bytes32,uint256)"
            )
        );
        assertEq(sel, expected, "selector != canonical v0.7 encoding");
        assertEq(sel, V07_SELECTOR, "selector != 0x52b7512c");
    }

    function test_OldBrokenSelectorNoLongerPresent() public pure {
        bytes4 sel = SincorPaymaster.validatePaymasterUserOp.selector;
        bytes4 oldBroken = bytes4(keccak256("validatePaymasterUserOp(bytes,bytes32,uint256)"));
        assertTrue(sel != oldBroken, "still exposes the broken bytes-based selector");
    }

    // ── (b) validation logic works through the corrected signature ───────

    function test_ValidateAcceptsAllowlistedSenderViaStruct() public {
        _openSponsorship();
        (bytes memory ctx, uint256 vd) = _asEntryPointValidate(agent, 1 ether);
        assertEq(vd, 0, "validationData must be 0 on accept");
        (address s, uint256 c) = abi.decode(ctx, (address, uint256));
        assertEq(s, agent, "context sender must come from userOp.sender");
        assertEq(c, 1 ether, "context maxCost mismatch");
        assertEq(pm.inFlight(agent), 1, "in-flight not marked");
        assertEq(pm.reservedWei(), 1 ether, "reservation not booked");
    }

    function test_ValidateRevertsWhenCallerIsNotEntryPoint() public {
        _openSponsorship();
        vm.expectRevert("only EntryPoint");
        pm.validatePaymasterUserOp(_userOp(agent), bytes32(0), 1 ether);
    }

    function test_ValidateRevertsForNonAllowlistedSender() public {
        // Attacker crafts a userOp naming THEMSELVES as sender: still rejected.
        pm.setMaxSponsoredWei(10 ether);
        vm.prank(address(ep));
        vm.expectRevert("not allowlisted");
        pm.validatePaymasterUserOp(_userOp(attacker), bytes32(0), 1 ether);
        assertEq(pm.reservedWei(), 0, "rejected op must reserve nothing");
    }

    function test_ValidateRevertsAtOpCap() public {
        _openSponsorship();
        pm.setMaxSponsoredOps(1);
        _asEntryPointValidate(agent, 1 ether);
        vm.prank(address(ep));
        vm.expectRevert("op cap reached");
        pm.validatePaymasterUserOp(_userOp(agent), bytes32(0), 1 ether);
    }

    function test_ValidateRevertsOverSpendCeiling() public {
        pm.setProbation(agent, true);
        pm.setMaxSponsoredWei(0.5 ether);
        vm.prank(address(ep));
        vm.expectRevert("spend ceiling");
        pm.validatePaymasterUserOp(_userOp(agent), bytes32(0), 1 ether);
    }

    // ── (c) withdrawTo moves funds only for authorized callers ───────────

    function test_WithdrawToMovesFundsForOwner() public {
        pm.deposit{value: 5 ether}();
        assertEq(ep.balanceOf(address(pm)), 5 ether, "deposit not credited");

        uint256 before = recipient.balance;
        vm.expectEmit(true, false, false, true);
        emit Withdrawn(recipient, 2 ether);
        pm.withdrawTo(payable(recipient), 2 ether);

        assertEq(recipient.balance - before, 2 ether, "recipient not paid");
        assertEq(ep.balanceOf(address(pm)), 3 ether, "deposit not debited");
    }

    function test_WithdrawToMovesFundsForAuthorizedWithdrawer() public {
        pm.deposit{value: 5 ether}();
        vm.expectEmit(true, false, false, true);
        emit WithdrawerSet(withdrawer);
        pm.setWithdrawer(withdrawer);
        assertEq(pm.withdrawer(), withdrawer);

        uint256 before = recipient.balance;
        vm.prank(withdrawer);
        pm.withdrawTo(payable(recipient), 5 ether);
        assertEq(recipient.balance - before, 5 ether, "withdrawer could not withdraw");
        assertEq(ep.balanceOf(address(pm)), 0, "full deposit should be drained");
    }

    function test_SetWithdrawerOnlyOwner() public {
        vm.prank(attacker);
        vm.expectRevert("not owner");
        pm.setWithdrawer(attacker);
    }

    function test_WithdrawToRevertsZeroRecipient() public {
        pm.deposit{value: 1 ether}();
        vm.expectRevert("zero recipient");
        pm.withdrawTo(payable(address(0)), 1 ether);
    }

    function test_WithdrawToRevertsWhenEntryPointBalanceShort() public {
        pm.deposit{value: 1 ether}();
        vm.expectRevert("insufficient deposit");
        pm.withdrawTo(payable(recipient), 2 ether);
    }

    // ── (d) unauthorized withdrawal is impossible ─────────────────────────

    function test_UnauthorizedWithdrawReverts() public {
        pm.deposit{value: 5 ether}();
        pm.setWithdrawer(withdrawer);

        // Random attacker.
        vm.prank(attacker);
        vm.expectRevert("not authorized");
        pm.withdrawTo(payable(attacker), 5 ether);

        // The EntryPoint itself is not authorized either.
        vm.prank(address(ep));
        vm.expectRevert("not authorized");
        pm.withdrawTo(payable(attacker), 5 ether);

        // Even the allowlisted agent cannot withdraw.
        vm.prank(agent);
        vm.expectRevert("not authorized");
        pm.withdrawTo(payable(agent), 5 ether);

        assertEq(ep.balanceOf(address(pm)), 5 ether, "funds must be untouched");
    }

    function test_WithdrawerRevocationDisablesWithdrawals() public {
        pm.deposit{value: 5 ether}();
        pm.setWithdrawer(withdrawer);
        pm.setWithdrawer(address(0));
        assertEq(pm.withdrawer(), address(0));

        vm.prank(withdrawer);
        vm.expectRevert("not authorized");
        pm.withdrawTo(payable(recipient), 1 ether);

        // Owner retains access after revoking the separate withdrawer.
        pm.withdrawTo(payable(recipient), 1 ether);
        assertEq(ep.balanceOf(address(pm)), 4 ether);
    }
}
