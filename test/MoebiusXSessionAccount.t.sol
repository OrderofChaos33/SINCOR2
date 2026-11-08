// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {ERC20Mock} from "@openzeppelin/contracts/mocks/token/ERC20Mock.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {MoebiusXSessionAccount} from "../contracts/security/MoebiusXSessionAccount.sol";

/// @notice Mock Base DEX router: pulls the input token from the caller via
///         transferFrom, exactly like a real swap router. This is what makes
///         the account's balance-delta spend accounting bind to reality.
contract MockDexRouter {
    function swap(address tokenIn, uint256 amountIn) external returns (uint256) {
        require(IERC20(tokenIn).transferFrom(msg.sender, address(this), amountIn), "pull failed");
        return amountIn;
    }

    function ping() external pure returns (uint256) {
        return 1;
    }
}

/// @notice A contract that is never allowlisted.
contract MockTarget {
    function ping() external pure returns (uint256) {
        return 42;
    }
}

contract MoebiusXSessionAccountTest is Test {
    // The production owner, mirrored in sim: the founder's treasury EOA.
    address public constant OWNER = 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac;

    uint256 public constant CAP = 25e6; // 25 USDC (6 decimals) total envelope
    uint256 public constant PER_CALL = 15e6; // 15 USDC per-call cap

    ERC20Mock public usdc;
    MoebiusXSessionAccount public account;
    MockDexRouter public router;
    MockTarget public otherTarget;

    address public sessionKey = vm.addr(0x5E5510);
    bytes32 public sessionId = keccak256("moebiusx-session-1");

    function setUp() public {
        usdc = new ERC20Mock();
        account = new MoebiusXSessionAccount(OWNER, IERC20(address(usdc)));
        router = new MockDexRouter();
        otherTarget = new MockTarget();

        // Fund the account ABOVE the session cap (100 USDC vs a 25 USDC cap)
        // so the tests isolate the cumulative-cap mechanism itself; the
        // account balance is only the outer backstop, never the test subject.
        usdc.mint(address(account), 100e6);

        // Owner grants the session and allowlists (router, swap).
        vm.startPrank(OWNER);
        account.grantSession(sessionId, sessionKey, 0, uint48(block.timestamp + 7 days), CAP, PER_CALL);
        account.allowCall(sessionId, address(router), MockDexRouter.swap.selector);
        // Owner gives the router a standing allowance. In production this
        // would equal the session cap (defense in depth); the tests set it to
        // the full funded amount so the CUMULATIVE CAP ITSELF is provably the
        // rejecting mechanism, not the allowance. The session key can never
        // raise any allowance (the spend token is not an allowlisted target).
        account.executeAsOwner(
            address(usdc), 0, abi.encodeWithSelector(IERC20.approve.selector, address(router), 100e6)
        );
        vm.stopPrank();
    }

    function _swap(uint256 amount) internal {
        vm.prank(sessionKey);
        account.executeAsSession(
            sessionId, address(router), abi.encodeWithSelector(MockDexRouter.swap.selector, address(usdc), amount)
        );
    }

    // --- Property 1: allowed action within cap succeeds ---------------------

    function test_SessionExecutesAllowedSwapWithinCap() public {
        _swap(10e6);
        assertEq(usdc.balanceOf(address(router)), 10e6);
        assertEq(usdc.balanceOf(address(account)), 90e6);
        (, , , , uint256 spent, , , ) = account.sessions(sessionId);
        assertEq(spent, 10e6);
    }

    // --- Property 2: cumulative cap cannot be exceeded ----------------------

    function test_CumulativeCapEnforcedAcrossSplitCalls() public {
        _swap(15e6);
        // Splitting does not help: 15 + 15 = 30 > 25 cumulative cap.
        vm.prank(sessionKey);
        vm.expectRevert(MoebiusXSessionAccount.CumulativeCapExceeded.selector);
        account.executeAsSession(
            sessionId, address(router), abi.encodeWithSelector(MockDexRouter.swap.selector, address(usdc), 15e6)
        );
        // The reverted call settled nothing.
        assertEq(usdc.balanceOf(address(account)), 85e6);
        (, , , , uint256 spent, , , ) = account.sessions(sessionId);
        assertEq(spent, 15e6);
    }

    function test_CumulativeCapEnforcedOnSingleLargeCall() public {
        bytes32 id2 = keccak256("moebiusx-session-2");
        vm.startPrank(OWNER);
        account.grantSession(id2, sessionKey, 0, uint48(block.timestamp + 7 days), CAP, 100e6);
        account.allowCall(id2, address(router), MockDexRouter.swap.selector);
        vm.stopPrank();

        vm.prank(sessionKey);
        vm.expectRevert(MoebiusXSessionAccount.CumulativeCapExceeded.selector);
        account.executeAsSession(
            id2, address(router), abi.encodeWithSelector(MockDexRouter.swap.selector, address(usdc), 26e6)
        );
    }

    function test_PerCallCapEnforced() public {
        vm.prank(sessionKey);
        vm.expectRevert(MoebiusXSessionAccount.PerCallCapExceeded.selector);
        account.executeAsSession(
            sessionId, address(router), abi.encodeWithSelector(MockDexRouter.swap.selector, address(usdc), 16e6)
        );
    }

    // --- Property 3: non-allowlisted target / selector is refused -----------

    function test_NonAllowlistedTargetReverts() public {
        vm.prank(sessionKey);
        vm.expectRevert(MoebiusXSessionAccount.CallNotAllowlisted.selector);
        account.executeAsSession(sessionId, address(otherTarget), abi.encodeWithSelector(MockTarget.ping.selector));
    }

    function test_AllowlistedTargetWrongSelectorReverts() public {
        vm.prank(sessionKey);
        vm.expectRevert(MoebiusXSessionAccount.CallNotAllowlisted.selector);
        account.executeAsSession(sessionId, address(router), abi.encodeWithSelector(MockDexRouter.ping.selector));
    }

    function test_SessionCannotCallSpendTokenDirectly() public {
        // A direct USDC transfer by the session key would bypass accounting;
        // the token is not allowlisted, so the call is refused.
        vm.prank(sessionKey);
        vm.expectRevert(MoebiusXSessionAccount.CallNotAllowlisted.selector);
        account.executeAsSession(
            sessionId, address(usdc), abi.encodeWithSelector(IERC20.transfer.selector, address(0xBEEF), 1e6)
        );
    }

    function test_OwnerCannotAllowlistSpendTokenOrAccount() public {
        vm.startPrank(OWNER);
        vm.expectRevert("forbidden target");
        account.allowCall(sessionId, address(usdc), IERC20.transfer.selector);
        vm.expectRevert("forbidden target");
        account.allowCall(sessionId, address(account), MoebiusXSessionAccount.executeAsOwner.selector);
        vm.stopPrank();
    }

    // --- Property 4: owner revocation blocks the session --------------------

    function test_OwnerRevocationBlocksSession() public {
        _swap(5e6); // works before revocation
        vm.prank(OWNER);
        account.revokeSession(sessionId);

        vm.prank(sessionKey);
        vm.expectRevert(MoebiusXSessionAccount.SessionRevokedErr.selector);
        account.executeAsSession(
            sessionId, address(router), abi.encodeWithSelector(MockDexRouter.swap.selector, address(usdc), 1e6)
        );
    }

    function test_OwnerRetainsFullControlAfterRevocation() public {
        _swap(5e6);
        vm.startPrank(OWNER);
        account.revokeSession(sessionId);
        // Owner drains the remaining balance back to the treasury address.
        account.executeAsOwner(
            address(usdc), 0, abi.encodeWithSelector(IERC20.transfer.selector, OWNER, 95e6)
        );
        vm.stopPrank();
        assertEq(usdc.balanceOf(OWNER), 95e6);
        assertEq(usdc.balanceOf(address(account)), 0);
    }

    // --- Time window and identity -------------------------------------------

    function test_SessionExpires() public {
        vm.warp(block.timestamp + 8 days);
        vm.prank(sessionKey);
        vm.expectRevert(MoebiusXSessionAccount.SessionExpired.selector);
        account.executeAsSession(
            sessionId, address(router), abi.encodeWithSelector(MockDexRouter.swap.selector, address(usdc), 1e6)
        );
    }

    function test_ImpostorCannotExecuteAsSession() public {
        vm.prank(address(0x1BAD));
        vm.expectRevert(MoebiusXSessionAccount.NotSessionKey.selector);
        account.executeAsSession(
            sessionId, address(router), abi.encodeWithSelector(MockDexRouter.swap.selector, address(usdc), 1e6)
        );
    }

    function test_NonOwnerCannotGrantOrRevoke() public {
        vm.prank(address(0x1BAD));
        vm.expectRevert(MoebiusXSessionAccount.NotOwner.selector);
        account.grantSession(keccak256("x"), sessionKey, 0, uint48(block.timestamp + 1 days), CAP, PER_CALL);

        vm.prank(address(0x1BAD));
        vm.expectRevert(MoebiusXSessionAccount.NotOwner.selector);
        account.revokeSession(sessionId);
    }

    // ── Fuzz: cage enforcement under fuzzed amounts/targets/calldata ──

    function _swapData(uint256 amount) internal view returns (bytes memory) {
        return abi.encodeWithSelector(MockDexRouter.swap.selector, address(usdc), amount);
    }

    function _spent(bytes32 id) internal view returns (uint256 spent) {
        (, , , , spent, , , ) = account.sessions(id);
    }

    function testFuzz_PerCallCapBoundary(uint256 amount) public {
        amount = bound(amount, 0, 100e6);
        if (amount > PER_CALL) {
            vm.prank(sessionKey);
            vm.expectRevert(MoebiusXSessionAccount.PerCallCapExceeded.selector);
            account.executeAsSession(sessionId, address(router), _swapData(amount));
            assertEq(_spent(sessionId), 0);
            assertEq(usdc.balanceOf(address(account)), 100e6);
        } else {
            vm.prank(sessionKey);
            account.executeAsSession(sessionId, address(router), _swapData(amount));
            assertEq(_spent(sessionId), amount);
            assertEq(usdc.balanceOf(address(account)), 100e6 - amount);
        }
    }

    function testFuzz_CumulativeCapNeverExceeded(uint256 a1, uint256 a2, uint256 a3) public {
        uint256[3] memory amounts = [bound(a1, 0, 20e6), bound(a2, 0, 20e6), bound(a3, 0, 20e6)];
        uint256 expectedSpent;
        for (uint256 i = 0; i < 3; i++) {
            uint256 amt = amounts[i];
            bool ok;
            vm.prank(sessionKey);
            try account.executeAsSession(sessionId, address(router), _swapData(amt)) {
                ok = true;
            } catch {
                ok = false;
            }
            if (ok) {
                // A success is only legitimate inside both caps.
                assertLe(amt, PER_CALL, "over-per-call swap succeeded");
                expectedSpent += amt;
                assertLe(expectedSpent, CAP, "cumulative cap exceeded");
            } else {
                // A revert is only legitimate outside a cap: the router call
                // itself cannot fail for an in-cage amount (funded + approved).
                assertTrue(amt > PER_CALL || expectedSpent + amt > CAP, "in-cage swap reverted");
            }
            assertEq(_spent(sessionId), expectedSpent);
            assertEq(usdc.balanceOf(address(account)), 100e6 - expectedSpent);
        }
    }

    function testFuzz_ProductionCageShape(uint256 amount) public {
        // The production session cage: $5 per trade, $25 total (6dp USDC).
        bytes32 id = keccak256("moebiusx-prod-cage");
        vm.startPrank(OWNER);
        account.grantSession(id, sessionKey, 0, uint48(block.timestamp + 7 days), 25e6, 5e6);
        account.allowCall(id, address(router), MockDexRouter.swap.selector);
        vm.stopPrank();

        amount = bound(amount, 0, 30e6);
        if (amount > 5e6) {
            vm.prank(sessionKey);
            vm.expectRevert(MoebiusXSessionAccount.PerCallCapExceeded.selector);
            account.executeAsSession(id, address(router), _swapData(amount));
            assertEq(_spent(id), 0);
        } else {
            vm.prank(sessionKey);
            account.executeAsSession(id, address(router), _swapData(amount));
            assertEq(_spent(id), amount);
        }
    }

    function testFuzz_NonAllowlistedTargetAlwaysReverts(address fuzzTarget) public {
        vm.assume(fuzzTarget != address(router));
        vm.prank(sessionKey);
        vm.expectRevert(MoebiusXSessionAccount.CallNotAllowlisted.selector);
        account.executeAsSession(sessionId, fuzzTarget, _swapData(1e6));
    }

    function testFuzz_WrongSelectorOnRouterAlwaysReverts(bytes4 fuzzSelector) public {
        vm.assume(fuzzSelector != MockDexRouter.swap.selector);
        bytes memory data = abi.encodePacked(fuzzSelector, bytes32(uint256(1)), bytes32(uint256(1)));
        vm.prank(sessionKey);
        vm.expectRevert(MoebiusXSessionAccount.CallNotAllowlisted.selector);
        account.executeAsSession(sessionId, address(router), data);
    }

    function testFuzz_ArbitraryCalldataAccountingInvariant(bytes calldata data) public {
        // Any calldata at all — valid swap encodings, wrong selectors, short
        // or garbage payloads: the call may succeed or revert, but the spend
        // accounting may never break. Every unit of outflow must be charged.
        vm.prank(sessionKey);
        try account.executeAsSession(sessionId, address(router), data) {} catch {}

        uint256 spent = _spent(sessionId);
        assertLe(spent, PER_CALL);
        assertLe(spent, CAP);
        assertEq(usdc.balanceOf(address(account)), 100e6 - spent);
        assertEq(usdc.balanceOf(address(router)), spent);
    }

    function testFuzz_ImpostorAlwaysReverts(address caller, uint256 amount) public {
        vm.assume(caller != sessionKey);
        amount = bound(amount, 0, 25e6);
        vm.prank(caller);
        vm.expectRevert(MoebiusXSessionAccount.NotSessionKey.selector);
        account.executeAsSession(sessionId, address(router), _swapData(amount));
    }

    function testFuzz_TimeWindowBoundary(uint256 warpBy) public {
        warpBy = bound(warpBy, 0, 14 days);
        vm.warp(block.timestamp + warpBy);
        if (warpBy > 7 days) {
            vm.prank(sessionKey);
            vm.expectRevert(MoebiusXSessionAccount.SessionExpired.selector);
            account.executeAsSession(sessionId, address(router), _swapData(1e6));
        } else {
            vm.prank(sessionKey);
            account.executeAsSession(sessionId, address(router), _swapData(1e6));
            assertEq(_spent(sessionId), 1e6);
        }
    }

    function testFuzz_NonOwnerCannotGrantOrRevoke(address caller) public {
        vm.assume(caller != OWNER);
        vm.prank(caller);
        vm.expectRevert(MoebiusXSessionAccount.NotOwner.selector);
        account.grantSession(keccak256("fuzz-grant"), sessionKey, 0, uint48(block.timestamp + 1 days), CAP, PER_CALL);

        vm.prank(caller);
        vm.expectRevert(MoebiusXSessionAccount.NotOwner.selector);
        account.revokeSession(sessionId);
    }
}
