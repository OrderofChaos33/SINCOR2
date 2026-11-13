// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {
    SealAuthorization,
    SealEscrowVault,
    SealFactoring
} from "../contracts/SealFactoring.sol";

contract Round2Token is ERC20 {
    constructor() ERC20("Round2 USDC", "r2USDC") {}
    function mint(address to, uint256 amount) external { _mint(to, amount); }
}

/// @notice 10% fee-on-transfer token (same mechanics as the other suites).
contract Round2FotToken is ERC20 {
    constructor() ERC20("Round2FoT", "r2FOT") {}
    function mint(address to, uint256 amount) external { _mint(to, amount); }
    function transfer(address to, uint256 amount) public override returns (bool) {
        uint256 fee = amount / 10;
        if (fee > 0) _burn(msg.sender, fee);
        return super.transfer(to, amount - fee);
    }
    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        uint256 fee = amount / 10;
        bool ok = super.transferFrom(from, to, amount - fee);
        if (fee > 0) _burn(from, fee);
        return ok;
    }
}

/// @notice Token whose transfer hook tries to reenter the factoring
///         contract's reclaim path mid-transfer.
contract Round2ReentrantToken is ERC20 {
    SealFactoring public target;
    bytes32 public atkTaskId;
    bool public armed;
    bool public reentrySucceeded;

    constructor() ERC20("Round2Reent", "r2REENT") {}
    function mint(address to, uint256 amount) external { _mint(to, amount); }
    function arm(SealFactoring _target, bytes32 _taskId) external {
        target = _target;
        atkTaskId = _taskId;
        armed = true;
        reentrySucceeded = false;
    }
    function transfer(address to, uint256 amount) public override returns (bool) {
        bool ok = super.transfer(to, amount);
        if (armed) {
            armed = false;
            try target.reclaimLockedEscrow(atkTaskId) {
                reentrySucceeded = true;
            } catch {}
        }
        return ok;
    }
}

/**
 * @notice ROUND-2 FIX TESTS — Lane 4 SealFactoring.
 *         Covers: (1) constructor role-collision now reverts at deploy;
 *         (2) the new no-advance reclaim exit for locked escrow.
 */
contract SealFactoringRound2Test is Test {
    Round2Token internal token;
    SealEscrowVault internal vault;
    SealFactoring internal factoring;

    uint256 internal buyerPk = 0xB0B;
    address internal buyer;
    address internal worker = address(0x1002);
    address internal treasury = address(0x1003);
    address internal admin = address(0x1004);
    address internal settler = address(0x1005);
    address internal funder = address(0x1006);
    address internal payee1 = address(0x1007);
    address internal stranger = address(0x1010);
    address internal evilModule = address(0x666);

    uint256 internal constant GROSS = 1_000_000;
    bytes32 internal schemaHash = keccak256("task-spec-v1");

    function setUp() public {
        buyer = vm.addr(buyerPk);
        token = new Round2Token();
        vault = new SealEscrowVault(address(token), admin);
        factoring = new SealFactoring(address(vault), treasury, admin, settler, 20, 8_000);
        vm.prank(admin);
        vault.setSettlementModule(address(factoring));
        vm.prank(admin);
        factoring.setPayeeAllowlist(payee1, true);

        token.mint(buyer, 10_000_000);
        token.mint(funder, 10_000_000);
        token.mint(worker, 1_000_000);
        vm.prank(buyer);
        token.approve(address(vault), type(uint256).max);
        vm.prank(buyer);
        token.approve(address(factoring), type(uint256).max);
        vm.prank(funder);
        token.approve(address(factoring), type(uint256).max);
        vm.prank(funder);
        factoring.fundLiquidity(4_000_000);
    }

    // --- helpers ------------------------------------------------------------

    function _registerWith(SealEscrowVault v, SealAuthorization memory seal, uint256 pk) internal {
        (uint8 vv, bytes32 r, bytes32 s) = vm.sign(pk, v.hashSeal(seal));
        v.registerSeal(seal, vv, r, s);
    }

    function _lock(bytes32 taskId, uint256 amt) internal {
        SealAuthorization memory seal = SealAuthorization({
            taskId: taskId, buyer: buyer, provider: worker, amount: amt,
            expectedSchemaHash: schemaHash, deadline: block.timestamp + 7 days,
            nonce: vault.nonces(buyer)
        });
        _registerWith(vault, seal, buyerPk);
        vault.lockEscrow(taskId);
    }

    // =====================================================================
    // FIX 1 — constructor role collision
    // =====================================================================

    function testR2_Constructor_SettlerEqualsAdmin_Reverts() public {
        SealEscrowVault v2 = new SealEscrowVault(address(token), admin);
        // verifier defaults to admin; settler := admin would collapse the
        // two-key control onto a single key. Deployment must revert.
        vm.expectRevert(SealFactoring.RolesMustBeSeparate.selector);
        new SealFactoring(address(v2), treasury, admin, admin, 20, 8_000);
    }

    function testR2_Constructor_DistinctRoles_DeployAndRolesSeparate() public {
        SealEscrowVault v2 = new SealEscrowVault(address(token), admin);
        SealFactoring f2 = new SealFactoring(address(v2), treasury, admin, settler, 20, 8_000);
        assertEq(f2.verifier(), admin, "verifier defaults to admin");
        assertEq(f2.settler(), settler);
        assertTrue(f2.verifier() != f2.settler(), "roles distinct from construction");
    }

    // =====================================================================
    // FIX 2 — no-advance reclaim
    // =====================================================================

    function testR2_Reclaim_DustLock_HappyPath_ExactAmounts_CountOnce() public {
        bytes32 taskId = keccak256("r2-reclaim-dust");
        uint256 buyerBefore = token.balanceOf(buyer);
        _lock(taskId, 500); // gross 500: max advance 380 < MIN_ADVANCE 1_000
        assertEq(token.balanceOf(buyer), buyerBefore - 500);
        assertEq(token.balanceOf(address(vault)), 500);
        assertEq(vault.activeLockedCount(), 1);
        assertTrue(vault.isLocked(taskId));

        vm.prank(buyer);
        uint256 reclaimed = factoring.reclaimLockedEscrow(taskId);

        assertEq(reclaimed, 500, "reclaim returns the full locked (received) amount");
        assertEq(token.balanceOf(buyer), buyerBefore, "buyer made exactly whole");
        assertEq(token.balanceOf(address(vault)), 0, "vault fully drained, nothing stranded");
        assertEq(vault.activeLockedCount(), 0, "count decremented exactly once (1 -> 0)");
        assertFalse(vault.isLocked(taskId), "no longer locked");
        (SealFactoring.AdvanceState st,,,,,,) = factoring.advances(taskId);
        assertEq(uint256(st), uint256(SealFactoring.AdvanceState.None), "no advance was ever created");
    }

    function testR2_Reclaim_NormalLock_NoAdvance_ImmediateReclaim() public {
        // The rule is "no advance exists" — NOT dust-only and NOT gated on
        // the seal deadline: a buyer may withdraw their own unencumbered
        // escrow at any time before drawing an advance against it.
        bytes32 taskId = keccak256("r2-reclaim-normal");
        uint256 buyerBefore = token.balanceOf(buyer);
        _lock(taskId, 250_000);
        assertEq(vault.activeLockedCount(), 1);
        vm.prank(buyer);
        uint256 reclaimed = factoring.reclaimLockedEscrow(taskId);
        assertEq(reclaimed, 250_000);
        assertEq(token.balanceOf(buyer), buyerBefore, "full amount back, pre-deadline");
        assertEq(vault.activeLockedCount(), 0);
    }

    function testR2_Reclaim_NonBuyer_Reverts() public {
        bytes32 taskId = keccak256("r2-reclaim-nonbuyer");
        _lock(taskId, 500);
        address[4] memory callers = [worker, stranger, settler, admin];
        for (uint256 i = 0; i < callers.length; i++) {
            vm.prank(callers[i]);
            vm.expectRevert(SealFactoring.Unauthorized.selector);
            factoring.reclaimLockedEscrow(taskId);
        }
        // Failed attempts changed nothing: still locked, count intact, and
        // the real buyer can still reclaim afterwards.
        assertTrue(vault.isLocked(taskId));
        assertEq(vault.activeLockedCount(), 1);
        assertEq(token.balanceOf(address(vault)), 500);
        vm.prank(buyer);
        factoring.reclaimLockedEscrow(taskId);
        assertEq(vault.activeLockedCount(), 0);
    }

    function testR2_Reclaim_WithActiveAdvance_Reverts_AndAdvanceUnaffected() public {
        bytes32 taskId = keccak256("r2-reclaim-active");
        _lock(taskId, GROSS);
        factoring.issueAdvance(taskId, 100_000);
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.ReclaimBlockedByActiveAdvance.selector);
        factoring.reclaimLockedEscrow(taskId);
        // Nothing moved: escrow still locked in service of the advance.
        assertTrue(vault.isLocked(taskId));
        assertEq(vault.activeLockedCount(), 1);
        assertEq(token.balanceOf(address(vault)), GROSS);
        // Even a partially-spent advance still blocks the reclaim.
        vm.prank(worker);
        factoring.spendAdvance(taskId, payee1, 40_000);
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.ReclaimBlockedByActiveAdvance.selector);
        factoring.reclaimLockedEscrow(taskId);
        // And the advance lifecycle still completes normally afterwards.
        vm.prank(admin); // verifier == admin
        factoring.attestVerification(taskId, keccak256("sigil"));
        vm.prank(settler);
        factoring.settle(taskId, keccak256("sigil"));
        assertEq(vault.activeLockedCount(), 0, "settlement - not reclaim - closed this lock");
    }

    function testR2_Reclaim_DoubleReclaim_Reverts() public {
        bytes32 taskId = keccak256("r2-reclaim-double");
        uint256 buyerBefore = token.balanceOf(buyer);
        _lock(taskId, 500);
        vm.prank(buyer);
        factoring.reclaimLockedEscrow(taskId);
        assertEq(token.balanceOf(buyer), buyerBefore);
        // Second reclaim: the lock is terminally closed, isLocked is false.
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.EscrowNotLocked.selector);
        factoring.reclaimLockedEscrow(taskId);
        assertEq(token.balanceOf(buyer), buyerBefore, "no double-pay");
        assertEq(vault.activeLockedCount(), 0, "count not decremented twice (no underflow)");
        // The taskId is dead for every other path too: no advance can be
        // issued against reclaimed escrow, ever.
        vm.expectRevert(SealFactoring.EscrowNotLocked.selector);
        factoring.issueAdvance(taskId, 1_000);
    }

    function testR2_Reclaim_NotLocked_Reverts() public {
        // Registered but never locked.
        bytes32 registered = keccak256("r2-reclaim-unlocked");
        SealAuthorization memory seal = SealAuthorization({
            taskId: registered, buyer: buyer, provider: worker, amount: 500,
            expectedSchemaHash: schemaHash, deadline: block.timestamp + 7 days,
            nonce: vault.nonces(buyer)
        });
        _registerWith(vault, seal, buyerPk);
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.EscrowNotLocked.selector);
        factoring.reclaimLockedEscrow(registered);
        // Completely unknown taskId.
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.EscrowNotLocked.selector);
        factoring.reclaimLockedEscrow(keccak256("r2-no-such-task"));
    }

    function testR2_Reclaim_DirectVaultCall_Reverts() public {
        // The vault-side reclaim is module-only: a buyer (or anyone else)
        // cannot bypass the factoring contract's no-advance state check.
        bytes32 taskId = keccak256("r2-reclaim-direct-vault");
        _lock(taskId, 500);
        vm.prank(buyer);
        vm.expectRevert(SealEscrowVault.Unauthorized.selector);
        vault.reclaimLockedEscrow(taskId);
        vm.prank(stranger);
        vm.expectRevert(SealEscrowVault.Unauthorized.selector);
        vault.reclaimLockedEscrow(taskId);
        assertTrue(vault.isLocked(taskId));
        assertEq(vault.activeLockedCount(), 1);
    }

    function testR2_Reclaim_FotLock_ReturnsReceivedAmount() public {
        Round2FotToken ft = new Round2FotToken();
        SealEscrowVault v2 = new SealEscrowVault(address(ft), admin);
        SealFactoring f2 = new SealFactoring(address(v2), treasury, admin, settler, 20, 8_000);
        vm.prank(admin);
        v2.setSettlementModule(address(f2));
        ft.mint(buyer, 1_000_000);
        vm.prank(buyer);
        ft.approve(address(v2), type(uint256).max);

        bytes32 taskId = keccak256("r2-reclaim-fot");
        SealAuthorization memory seal = SealAuthorization({
            taskId: taskId, buyer: buyer, provider: worker, amount: 2_000,
            expectedSchemaHash: schemaHash, deadline: block.timestamp + 7 days, nonce: 0
        });
        _registerWith(v2, seal, buyerPk);
        v2.lockEscrow(taskId);
        // 10% burned in transit: exactly 1_800 received and accounted.
        assertEq(ft.balanceOf(address(v2)), 1_800);
        assertEq(v2.lockedAmountOf(taskId), 1_800);
        assertEq(v2.activeLockedCount(), 1);
        uint256 buyerAfterLock = ft.balanceOf(buyer); // 998_000 (2_000 left the buyer)

        vm.prank(buyer);
        uint256 reclaimed = f2.reclaimLockedEscrow(taskId);
        // The reclaim moves the RECEIVED amount (1_800), never the nominal
        // 2_000 — same finding-10 accounting as settle/refund. The token's
        // own 10% transfer fee applies on the way out (documented
        // residual): the buyer is credited 1_800 - 180 = 1_620 and the
        // vault drains to exactly zero (no phantom-amount wedge).
        assertEq(reclaimed, 1_800, "reclaim amount = received amount, not nominal 2_000");
        assertEq(ft.balanceOf(buyer) - buyerAfterLock, 1_620, "1_800 - 180 token transfer fee");
        assertEq(ft.balanceOf(address(v2)), 0, "vault fully drained");
        assertEq(v2.activeLockedCount(), 0, "count decremented exactly once");
        assertFalse(v2.isLocked(taskId));
    }

    function testR2_Repoint_StillBlockedWhileAdvanceActive() public {
        // Round-1 rule NOT weakened: escrow genuinely locked in service of
        // an advance still blocks the timelocked repoint, and a failed
        // reclaim attempt does not change that.
        bytes32 taskId = keccak256("r2-repoint-still-blocked");
        _lock(taskId, GROSS);
        factoring.issueAdvance(taskId, 100_000);
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.ReclaimBlockedByActiveAdvance.selector);
        factoring.reclaimLockedEscrow(taskId);
        vm.prank(admin);
        vault.setSettlementModule(evilModule);
        vm.warp(block.timestamp + vault.MODULE_CHANGE_DELAY() + 1);
        vm.prank(admin);
        vm.expectRevert(
            abi.encodeWithSelector(SealEscrowVault.ModuleChangeBlockedByActiveEscrow.selector, 1)
        );
        vault.executeSettlementModuleChange();
        assertEq(vault.settlementModule(), address(factoring), "module unchanged while advance active");
    }

    function testR2_Reclaim_ReentrantToken_ReentryBlocked_ExactOnce() public {
        Round2ReentrantToken rt = new Round2ReentrantToken();
        SealEscrowVault v2 = new SealEscrowVault(address(rt), admin);
        SealFactoring f2 = new SealFactoring(address(v2), treasury, admin, settler, 20, 8_000);
        vm.prank(admin);
        v2.setSettlementModule(address(f2));
        rt.mint(buyer, 1_000_000);
        vm.prank(buyer);
        rt.approve(address(v2), type(uint256).max);

        bytes32 taskId = keccak256("r2-reclaim-reenter");
        SealAuthorization memory seal = SealAuthorization({
            taskId: taskId, buyer: buyer, provider: worker, amount: 500,
            expectedSchemaHash: schemaHash, deadline: block.timestamp + 7 days, nonce: 0
        });
        _registerWith(v2, seal, buyerPk);
        v2.lockEscrow(taskId);
        uint256 buyerAfterLock = rt.balanceOf(buyer);

        // The token hook fires during the vault -> buyer reclaim transfer
        // and tries to reenter the reclaim. The ReentrancyGuard (plus the
        // vault's effects-before-transfer terminal flag) blocks it.
        rt.arm(f2, taskId);
        vm.prank(buyer);
        f2.reclaimLockedEscrow(taskId);
        assertFalse(rt.reentrySucceeded(), "reentrant reclaim blocked");
        assertEq(rt.balanceOf(buyer) - buyerAfterLock, 500, "buyer paid exactly once, exactly 500");
        assertEq(rt.balanceOf(address(v2)), 0);
        assertEq(v2.activeLockedCount(), 0, "count decremented exactly once despite reentry attempt");
    }
}
