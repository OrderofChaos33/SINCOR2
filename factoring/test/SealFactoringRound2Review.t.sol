// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {
    SealAuthorization,
    SealEscrowVault,
    SealFactoring
} from "../contracts/SealFactoring.sol";

contract R2RevToken is ERC20 {
    constructor() ERC20("R2Rev USDC", "revUSDC") {}
    function mint(address to, uint256 amount) external { _mint(to, amount); }
}

/// @notice 10% fee-on-transfer token.
contract R2RevFotToken is ERC20 {
    constructor() ERC20("R2RevFoT", "revFOT") {}
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

/// @notice Token whose transfer hook tries to reenter BOTH the factoring
///         reclaim and the vault reclaim mid-transfer.
contract R2RevReentrantToken is ERC20 {
    SealFactoring public targetF;
    SealEscrowVault public targetV;
    bytes32 public atkTaskId;
    bool public armed;
    bool public reentryFactoringSucceeded;
    bool public reentryVaultSucceeded;

    constructor() ERC20("R2RevReent", "revREENT") {}
    function mint(address to, uint256 amount) external { _mint(to, amount); }
    function arm(SealFactoring f, SealEscrowVault v, bytes32 t) external {
        targetF = f; targetV = v; atkTaskId = t; armed = true;
        reentryFactoringSucceeded = false; reentryVaultSucceeded = false;
    }
    function transfer(address to, uint256 amount) public override returns (bool) {
        bool ok = super.transfer(to, amount);
        if (armed) {
            armed = false;
            try targetF.reclaimLockedEscrow(atkTaskId) { reentryFactoringSucceeded = true; } catch {}
            try targetV.reclaimLockedEscrow(atkTaskId) { reentryVaultSucceeded = true; } catch {}
        }
        return ok;
    }
}

/**
 * @notice INDEPENDENT ROUND-2 CONFIRMATION REVIEW — Lane 4 SealFactoring.
 *         Written by the independent re-reviewer (did not write the fixes).
 *         Tests assert the SECURE post-round-2 behavior. A failing test
 *         means the attack succeeded = FINDING.
 */
contract SealFactoringRound2ReviewTest is Test {
    R2RevToken internal token;
    SealEscrowVault internal vault;
    SealFactoring internal factoring;

    uint256 internal buyerPk = 0xB0B;
    uint256 internal buyer2Pk = 0xB0C;
    address internal buyer;
    address internal buyer2;
    address internal worker = address(0x2002);
    address internal treasury = address(0x2003);
    address internal admin = address(0x2004);
    address internal settler = address(0x2005);
    address internal funder = address(0x2006);
    address internal payee1 = address(0x2007);
    address internal stranger = address(0x2010);
    address internal newRole = address(0x2011);
    address internal newRole2 = address(0x2012);
    address internal evilModule = address(0x666);

    uint256 internal constant GROSS = 1_000_000;
    bytes32 internal schemaHash = keccak256("task-spec-v1");
    bytes32 internal sigil = keccak256("sigil-r2rev");

    function setUp() public {
        buyer = vm.addr(buyerPk);
        buyer2 = vm.addr(buyer2Pk);
        token = new R2RevToken();
        vault = new SealEscrowVault(address(token), admin);
        factoring = new SealFactoring(address(vault), treasury, admin, settler, 20, 8_000);
        vm.prank(admin);
        vault.setSettlementModule(address(factoring));
        vm.prank(admin);
        factoring.setPayeeAllowlist(payee1, true);

        token.mint(buyer, 20_000_000);
        token.mint(buyer2, 20_000_000);
        token.mint(funder, 20_000_000);
        token.mint(worker, 1_000_000);
        vm.prank(buyer);
        token.approve(address(vault), type(uint256).max);
        vm.prank(buyer2);
        token.approve(address(vault), type(uint256).max);
        vm.prank(funder);
        token.approve(address(factoring), type(uint256).max);
        vm.prank(funder);
        factoring.fundLiquidity(10_000_000);
    }

    // --- helpers ------------------------------------------------------------

    function _registerWith(SealEscrowVault v, SealAuthorization memory seal, uint256 pk) internal {
        (uint8 vv, bytes32 r, bytes32 s) = vm.sign(pk, v.hashSeal(seal));
        v.registerSeal(seal, vv, r, s);
    }

    function _lockAmt(bytes32 taskId, uint256 amt) internal {
        _lockCustom(taskId, buyerPk, worker, amt, block.timestamp + 7 days);
    }

    function _lock(bytes32 taskId) internal { _lockAmt(taskId, GROSS); }

    function _lockCustom(bytes32 taskId, uint256 bPk, address provider, uint256 amt, uint256 deadline) internal {
        address b = vm.addr(bPk);
        SealAuthorization memory seal = SealAuthorization({
            taskId: taskId, buyer: b, provider: provider, amount: amt,
            expectedSchemaHash: schemaHash, deadline: deadline,
            nonce: vault.nonces(b)
        });
        _registerWith(vault, seal, bPk);
        vault.lockEscrow(taskId);
    }

    function _attestAndSettle(bytes32 taskId) internal {
        vm.prank(admin); // verifier == admin on a separated deploy
        factoring.attestVerification(taskId, sigil);
        vm.prank(settler);
        factoring.settle(taskId, sigil);
    }

    function _state(bytes32 taskId) internal view returns (uint256) {
        (SealFactoring.AdvanceState st,,,,,,) = factoring.advances(taskId);
        return uint256(st);
    }

    // =====================================================================
    // FIX 1 — CONSTRUCTOR ROLE SEPARATION
    // =====================================================================

    /// Non-vacuity: the SAME deploy parameters with distinct roles succeed
    /// and yield verifier != settler; only the colliding variant reverts.
    function testR2Rev_Ctor_CollisionReverts_DistinctDeployWorks() public {
        SealEscrowVault v2 = new SealEscrowVault(address(token), admin);
        vm.expectRevert(SealFactoring.RolesMustBeSeparate.selector);
        new SealFactoring(address(v2), treasury, admin, admin, 20, 8_000);

        SealFactoring ok = new SealFactoring(address(v2), treasury, admin, settler, 20, 8_000);
        assertEq(ok.verifier(), admin);
        assertEq(ok.settler(), settler);
        assertTrue(ok.verifier() != ok.settler());

        // Collision is admin-relative, not settler-address-specific: a
        // different admin colliding with its own settler also reverts.
        address admin2 = address(0x2099);
        SealEscrowVault v3 = new SealEscrowVault(address(token), admin2);
        vm.expectRevert(SealFactoring.RolesMustBeSeparate.selector);
        new SealFactoring(address(v3), treasury, admin2, admin2, 20, 8_000);
        SealFactoring ok2 = new SealFactoring(address(v3), treasury, admin2, settler, 20, 8_000);
        assertEq(ok2.verifier(), admin2);
        assertTrue(ok2.verifier() != ok2.settler());
    }

    /// On a correctly separated deploy the settler key ALONE cannot do the
    /// verifier's job and the verifier key ALONE cannot settle: the old
    /// single-key attest+settle is actually impossible, not just undeployed.
    function testR2Rev_Ctor_SingleKeyAttestPlusSettle_Impossible() public {
        bytes32 taskId = keccak256("r2rev-singlekey");
        _lock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        // Settler tries to attest (verifier's job).
        vm.prank(settler);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.attestVerification(taskId, sigil);
        // Settler tries to settle with NO attestation on record.
        vm.prank(settler);
        vm.expectRevert(SealFactoring.VerificationRequired.selector);
        factoring.settle(taskId, sigil);
        // Verifier (admin) tries to settle directly (settler's job).
        vm.prank(admin);
        factoring.attestVerification(taskId, sigil);
        vm.prank(admin);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.settle(taskId, sigil);
        // Buyer / worker / stranger can do neither.
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.attestVerification(taskId, sigil);
        // The legitimate TWO-key sequence is the only path and it works.
        vm.prank(settler);
        factoring.settle(taskId, sigil);
        assertEq(_state(taskId), uint256(SealFactoring.AdvanceState.Settled));
    }

    /// No other initialization path manufactures a collision: both setters
    /// guard, chained swap attempts guard at every step, non-admins cannot
    /// touch either setter.
    function testR2Rev_Ctor_NoSetterPathToCollision_InclSwapAttempts() public {
        // Direct collisions.
        vm.prank(admin);
        vm.expectRevert(SealFactoring.RolesMustBeSeparate.selector);
        factoring.setSettler(admin); // admin == current verifier
        vm.prank(admin);
        vm.expectRevert(SealFactoring.RolesMustBeSeparate.selector);
        factoring.setVerifier(settler); // settler == current settler
        // Swap attempt step 1: move verifier to newRole — now try settler=newRole.
        vm.prank(admin);
        factoring.setVerifier(newRole);
        vm.prank(admin);
        vm.expectRevert(SealFactoring.RolesMustBeSeparate.selector);
        factoring.setSettler(newRole);
        // Swap attempt step 2: move settler to newRole2 — now try verifier=newRole2.
        vm.prank(admin);
        factoring.setSettler(newRole2);
        vm.prank(admin);
        vm.expectRevert(SealFactoring.RolesMustBeSeparate.selector);
        factoring.setVerifier(newRole2);
        // Non-admin cannot rotate either role at all.
        vm.prank(stranger);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.setSettler(stranger);
        vm.prank(stranger);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.setVerifier(stranger);
        // Final state: roles rotated but STILL distinct.
        assertEq(factoring.verifier(), newRole);
        assertEq(factoring.settler(), newRole2);
        assertTrue(factoring.verifier() != factoring.settler());
    }

    // =====================================================================
    // FIX 2 — RECLAIM vs ADVANCE STATES
    // =====================================================================

    function testR2Rev_Reclaim_BlockedWhileAdvanceActive_InclPartialSpend() public {
        bytes32 taskId = keccak256("r2rev-reclaim-active");
        _lock(taskId);
        factoring.issueAdvance(taskId, 200_000);
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.ReclaimBlockedByActiveAdvance.selector);
        factoring.reclaimLockedEscrow(taskId);
        vm.prank(worker);
        factoring.spendAdvance(taskId, payee1, 75_000); // partial spend
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.ReclaimBlockedByActiveAdvance.selector);
        factoring.reclaimLockedEscrow(taskId);
        assertEq(vault.activeLockedCount(), 1);
        assertEq(token.balanceOf(address(vault)), GROSS);
        assertTrue(vault.isLocked(taskId));
    }

    function testR2Rev_Reclaim_BlockedWhileFailureDisputeOpen() public {
        bytes32 taskId = keccak256("r2rev-reclaim-dispute");
        _lock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        vm.prank(worker);
        factoring.spendAdvance(taskId, payee1, 10_000);
        vm.prank(buyer);
        factoring.markFailed(taskId); // dispute OPEN, advance still Active
        assertTrue(factoring.failureDisputed(taskId));
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.ReclaimBlockedByActiveAdvance.selector);
        factoring.reclaimLockedEscrow(taskId);
        assertEq(vault.activeLockedCount(), 1, "disputed escrow not reclaimed out from under the dispute");
        // Dispute resolves via finalize; only THEN is the escrow gone —
        // through the failure refund, not the reclaim.
        vm.warp(block.timestamp + factoring.DISPUTE_WINDOW());
        factoring.finalizeFailure(taskId);
        assertEq(vault.activeLockedCount(), 0);
        assertEq(_state(taskId), uint256(SealFactoring.AdvanceState.Defaulted));
    }

    function testR2Rev_Reclaim_AfterSettle_Reverts_NoDoubleDecrement() public {
        bytes32 taskId = keccak256("r2rev-reclaim-after-settle");
        _lock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        _attestAndSettle(taskId);
        assertEq(vault.activeLockedCount(), 0);
        uint256 buyerBal = token.balanceOf(buyer);
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.EscrowNotLocked.selector);
        factoring.reclaimLockedEscrow(taskId);
        assertEq(vault.activeLockedCount(), 0, "no second decrement / underflow");
        assertEq(token.balanceOf(buyer), buyerBal, "buyer not paid twice");
        assertEq(token.balanceOf(address(vault)), 0);
    }

    function testR2Rev_Reclaim_AfterDisputeFinalize_Reverts_NoDoubleDecrement() public {
        bytes32 taskId = keccak256("r2rev-reclaim-after-finalize");
        _lock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        vm.prank(buyer);
        factoring.markFailed(taskId);
        vm.warp(block.timestamp + factoring.DISPUTE_WINDOW());
        factoring.finalizeFailure(taskId);
        assertEq(vault.activeLockedCount(), 0);
        uint256 buyerBal = token.balanceOf(buyer);
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.EscrowNotLocked.selector);
        factoring.reclaimLockedEscrow(taskId);
        assertEq(vault.activeLockedCount(), 0);
        assertEq(token.balanceOf(buyer), buyerBal, "refund already paid; reclaim adds nothing");
    }

    function testR2Rev_Reclaim_AfterExpire_Reverts_NoDoubleDecrement() public {
        bytes32 taskId = keccak256("r2rev-reclaim-after-expire");
        _lock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        vm.warp(block.timestamp + 7 days + 1);
        factoring.expire(taskId);
        assertEq(vault.activeLockedCount(), 0);
        assertEq(_state(taskId), uint256(SealFactoring.AdvanceState.Defaulted));
        uint256 buyerBal = token.balanceOf(buyer);
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.EscrowNotLocked.selector);
        factoring.reclaimLockedEscrow(taskId);
        assertEq(vault.activeLockedCount(), 0);
        assertEq(token.balanceOf(buyer), buyerBal);
    }

    // =====================================================================
    // FIX 2 — THE RACE, BOTH ORDERINGS
    // =====================================================================

    function testR2Rev_Race_IssueFirst_ReclaimLoses_FundsConserved() public {
        bytes32 taskId = keccak256("r2rev-race-issue-first");
        _lock(taskId);
        uint256 totalBefore = token.balanceOf(buyer) + token.balanceOf(address(vault))
            + token.balanceOf(address(factoring)) + token.balanceOf(worker)
            + token.balanceOf(treasury) + token.balanceOf(payee1);
        factoring.issueAdvance(taskId, 100_000); // issue lands FIRST
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.ReclaimBlockedByActiveAdvance.selector);
        factoring.reclaimLockedEscrow(taskId); // reclaim loses cleanly
        // Lifecycle completes via settle; nothing exited twice.
        vm.prank(worker);
        factoring.spendAdvance(taskId, payee1, 40_000);
        _attestAndSettle(taskId);
        assertEq(vault.activeLockedCount(), 0);
        assertEq(token.balanceOf(address(vault)), 0);
        uint256 totalAfter = token.balanceOf(buyer) + token.balanceOf(address(vault))
            + token.balanceOf(address(factoring)) + token.balanceOf(worker)
            + token.balanceOf(treasury) + token.balanceOf(payee1);
        assertEq(totalAfter, totalBefore, "funds conserved in issue-first ordering");
    }

    function testR2Rev_Race_ReclaimFirst_IssueLoses_FundsConserved() public {
        bytes32 taskId = keccak256("r2rev-race-reclaim-first");
        uint256 buyerBefore = token.balanceOf(buyer);
        _lock(taskId);
        vm.prank(buyer);
        uint256 amt = factoring.reclaimLockedEscrow(taskId); // reclaim lands FIRST
        assertEq(amt, GROSS);
        vm.expectRevert(SealFactoring.EscrowNotLocked.selector);
        factoring.issueAdvance(taskId, 100_000); // issue loses cleanly
        assertEq(_state(taskId), 0, "no advance was created by the losing issue");
        assertEq(token.balanceOf(buyer), buyerBefore, "buyer exactly whole, no factor credit leaked");
        assertEq(token.balanceOf(address(vault)), 0);
        assertEq(vault.activeLockedCount(), 0, "single exit, single decrement");
        assertEq(factoring.totalOutstandingCredit(), 0);
    }

    // =====================================================================
    // FIX 2 — RECLAIM ABUSE
    // =====================================================================

    function testR2Rev_Reclaim_AllNonBuyerRoles_Revert() public {
        bytes32 taskId = keccak256("r2rev-reclaim-roles");
        _lock(taskId);
        address[6] memory callers = [worker, settler, admin, treasury, funder, stranger];
        for (uint256 i = 0; i < callers.length; i++) {
            vm.prank(callers[i]);
            vm.expectRevert(SealFactoring.Unauthorized.selector);
            factoring.reclaimLockedEscrow(taskId);
        }
        assertEq(vault.activeLockedCount(), 1, "failed abuse attempts never decrement");
        assertEq(token.balanceOf(address(vault)), GROSS);
    }

    function testR2Rev_Reclaim_PerSealBuyerBinding_OtherBuyerCannotTake() public {
        bytes32 taskId = keccak256("r2rev-reclaim-other-buyer");
        _lockCustom(taskId, buyer2Pk, worker, 500, block.timestamp + 7 days);
        // buyer (a real buyer elsewhere, but NOT this seal's buyer) cannot reclaim.
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.reclaimLockedEscrow(taskId);
        uint256 b2Before = token.balanceOf(buyer2);
        vm.prank(buyer2);
        factoring.reclaimLockedEscrow(taskId);
        assertEq(token.balanceOf(buyer2), b2Before + 500);
        assertEq(vault.activeLockedCount(), 0);
    }

    function testR2Rev_Reclaim_Second_Unknown_Unlocked_AllRevert() public {
        bytes32 taskId = keccak256("r2rev-reclaim-second");
        _lockAmt(taskId, 500);
        vm.prank(buyer);
        factoring.reclaimLockedEscrow(taskId);
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.EscrowNotLocked.selector);
        factoring.reclaimLockedEscrow(taskId); // second reclaim
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.EscrowNotLocked.selector);
        factoring.reclaimLockedEscrow(keccak256("r2rev-unknown")); // unknown
        // Registered but never locked.
        bytes32 unlocked = keccak256("r2rev-reclaim-unlocked");
        SealAuthorization memory seal = SealAuthorization({
            taskId: unlocked, buyer: buyer, provider: worker, amount: 500,
            expectedSchemaHash: schemaHash, deadline: block.timestamp + 7 days,
            nonce: vault.nonces(buyer)
        });
        _registerWith(vault, seal, buyerPk);
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.EscrowNotLocked.selector);
        factoring.reclaimLockedEscrow(unlocked);
        assertEq(vault.activeLockedCount(), 0, "no phantom decrements from any failed reclaim");
    }

    function testR2Rev_Reclaim_DirectVaultCall_AllNonModule_Revert() public {
        bytes32 taskId = keccak256("r2rev-reclaim-direct");
        _lockAmt(taskId, 500);
        address[5] memory callers = [buyer, worker, settler, admin, stranger];
        for (uint256 i = 0; i < callers.length; i++) {
            vm.prank(callers[i]);
            vm.expectRevert(SealEscrowVault.Unauthorized.selector);
            vault.reclaimLockedEscrow(taskId);
        }
        // Even the vault ADMIN cannot bypass the module's no-advance gate.
        assertTrue(vault.isLocked(taskId));
        assertEq(vault.activeLockedCount(), 1);
        // The legitimate module path still works afterwards.
        vm.prank(buyer);
        factoring.reclaimLockedEscrow(taskId);
        assertEq(vault.activeLockedCount(), 0);
    }

    function testR2Rev_Reclaim_ReentrantToken_BothReentriesFail_PaidOnce() public {
        R2RevReentrantToken rt = new R2RevReentrantToken();
        SealEscrowVault v2 = new SealEscrowVault(address(rt), admin);
        SealFactoring f2 = new SealFactoring(address(v2), treasury, admin, settler, 20, 8_000);
        vm.prank(admin);
        v2.setSettlementModule(address(f2));
        rt.mint(buyer, 1_000_000);
        vm.prank(buyer);
        rt.approve(address(v2), type(uint256).max);
        bytes32 taskId = keccak256("r2rev-reclaim-reenter");
        SealAuthorization memory seal = SealAuthorization({
            taskId: taskId, buyer: buyer, provider: worker, amount: 500,
            expectedSchemaHash: schemaHash, deadline: block.timestamp + 7 days, nonce: 0
        });
        _registerWith(v2, seal, buyerPk);
        v2.lockEscrow(taskId);
        uint256 buyerAfterLock = rt.balanceOf(buyer);
        rt.arm(f2, v2, taskId);
        vm.prank(buyer);
        f2.reclaimLockedEscrow(taskId);
        assertFalse(rt.reentryFactoringSucceeded(), "reentrant factoring reclaim blocked");
        assertFalse(rt.reentryVaultSucceeded(), "reentrant vault reclaim blocked");
        assertEq(rt.balanceOf(buyer) - buyerAfterLock, 500, "paid exactly once");
        assertEq(rt.balanceOf(address(v2)), 0);
        assertEq(v2.activeLockedCount(), 0);
    }

    function testR2Rev_Reclaim_FailedAttempts_DoNotDecrementOrPay() public {
        bytes32 taskId = keccak256("r2rev-reclaim-failed-attempts");
        _lockAmt(taskId, 500);
        for (uint256 i = 0; i < 3; i++) {
            vm.prank(stranger);
            vm.expectRevert(SealFactoring.Unauthorized.selector);
            factoring.reclaimLockedEscrow(taskId);
            vm.prank(stranger);
            vm.expectRevert(SealEscrowVault.Unauthorized.selector);
            vault.reclaimLockedEscrow(taskId);
        }
        assertEq(vault.activeLockedCount(), 1);
        assertEq(token.balanceOf(address(vault)), 500);
        assertEq(token.balanceOf(stranger), 0);
    }

    // =====================================================================
    // FIX 2 — COUNT INTEGRITY, MIXED SEQUENCE + REPOINT TIMING
    // =====================================================================

    function testR2Rev_CountIntegrity_MixedSequence_RepointExactlyAtZero() public {
        bytes32 tSettle = keccak256("r2rev-mix-settle");
        bytes32 tReclaim = keccak256("r2rev-mix-reclaim");
        bytes32 tExpire = keccak256("r2rev-mix-expire");
        bytes32 tLast = keccak256("r2rev-mix-last");
        _lock(tSettle);
        _lockAmt(tReclaim, 500);
        // Expire candidate gets a SHORT deadline so it can expire while
        // the others (7-day) remain live.
        _lockCustom(tExpire, buyerPk, worker, GROSS, block.timestamp + 1 days);
        _lockAmt(tLast, 700);
        assertEq(vault.activeLockedCount(), 4, "four locks");

        // 1) factored + settled: 4 -> 3
        factoring.issueAdvance(tSettle, 100_000);
        _attestAndSettle(tSettle);
        assertEq(vault.activeLockedCount(), 3, "after settle");

        // 2) reclaimed (no advance): 3 -> 2
        vm.prank(buyer);
        factoring.reclaimLockedEscrow(tReclaim);
        assertEq(vault.activeLockedCount(), 2, "after reclaim");

        // Advance the expire candidate and propose the repoint.
        factoring.issueAdvance(tExpire, 100_000);
        vm.prank(admin);
        vault.setSettlementModule(evilModule);
        vm.warp(block.timestamp + vault.MODULE_CHANGE_DELAY() + 1);
        // Timelock elapsed but count is 2: repoint MUST still be blocked.
        vm.prank(admin);
        vm.expectRevert(
            abi.encodeWithSelector(SealEscrowVault.ModuleChangeBlockedByActiveEscrow.selector, 2)
        );
        vault.executeSettlementModuleChange();

        // 3) expired: 2 -> 1 (past tExpire's 1-day deadline by now)
        factoring.expire(tExpire);
        assertEq(vault.activeLockedCount(), 1, "after expire");
        vm.prank(admin);
        vm.expectRevert(
            abi.encodeWithSelector(SealEscrowVault.ModuleChangeBlockedByActiveEscrow.selector, 1)
        );
        vault.executeSettlementModuleChange();
        assertEq(vault.settlementModule(), address(factoring), "repoint still blocked at count 1");

        // 4) last lock reclaimed: 1 -> 0, repoint executes NOW and only now.
        vm.prank(buyer);
        factoring.reclaimLockedEscrow(tLast);
        assertEq(vault.activeLockedCount(), 0, "after final reclaim");
        vm.prank(admin);
        vault.executeSettlementModuleChange();
        assertEq(vault.settlementModule(), evilModule, "repoint lands exactly at count 0");
    }

    // =====================================================================
    // FIX 2 — RECLAIMED taskId STAYS DEAD
    // =====================================================================

    function testR2Rev_ReclaimedTask_RelockAndReregister_Revert() public {
        bytes32 taskId = keccak256("r2rev-dead-relock");
        _lockAmt(taskId, 500);
        vm.prank(buyer);
        factoring.reclaimLockedEscrow(taskId);
        // Re-lock the same taskId directly in the vault: dead.
        vm.expectRevert(SealEscrowVault.SealAlreadyLocked.selector);
        vault.lockEscrow(taskId);
        // Re-register the same taskId with a fresh nonce/signature: dead.
        SealAuthorization memory seal = SealAuthorization({
            taskId: taskId, buyer: buyer, provider: worker, amount: 500,
            expectedSchemaHash: schemaHash, deadline: block.timestamp + 7 days,
            nonce: vault.nonces(buyer)
        });
        (uint8 vv, bytes32 r, bytes32 s) = vm.sign(buyerPk, vault.hashSeal(seal));
        vm.expectRevert(SealEscrowVault.SealAlreadyRegistered.selector);
        vault.registerSeal(seal, vv, r, s);
        assertEq(vault.activeLockedCount(), 0);
        assertEq(token.balanceOf(address(vault)), 0);
    }

    function testR2Rev_ReclaimedTask_IssueSettleExpireMarkFailed_AllDead() public {
        bytes32 taskId = keccak256("r2rev-dead-paths");
        _lockAmt(taskId, GROSS);
        vm.prank(buyer);
        factoring.reclaimLockedEscrow(taskId);
        vm.expectRevert(SealFactoring.EscrowNotLocked.selector);
        factoring.issueAdvance(taskId, 100_000);
        vm.prank(settler);
        vm.expectRevert(SealFactoring.NoActiveAdvance.selector);
        factoring.settle(taskId, sigil);
        vm.expectRevert(SealFactoring.AdvanceNotActive.selector);
        factoring.expire(taskId);
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.AdvanceNotActive.selector);
        factoring.markFailed(taskId);
        vm.expectRevert(SealFactoring.AdvanceNotActive.selector);
        factoring.finalizeFailure(taskId);
        assertEq(_state(taskId), 0, "advance state never resurrected");
        assertEq(vault.activeLockedCount(), 0);
    }

    /// Documented design: no deadline gate on a no-advance reclaim (no
    /// counterparty holds a claim). Even after the seal deadline the
    /// buyer's unencumbered dust must not re-strand.
    function testR2Rev_Reclaim_AfterDeadline_NoAdvance_StillExits() public {
        bytes32 taskId = keccak256("r2rev-reclaim-post-deadline");
        _lockCustom(taskId, buyerPk, worker, 500, block.timestamp + 1 days);
        vm.warp(block.timestamp + 2 days);
        uint256 buyerBefore = token.balanceOf(buyer);
        vm.prank(buyer);
        uint256 amt = factoring.reclaimLockedEscrow(taskId);
        assertEq(amt, 500);
        assertEq(token.balanceOf(buyer), buyerBefore + 500);
        assertEq(vault.activeLockedCount(), 0);
    }

    // =====================================================================
    // FIX 2 — FoT RECLAIM EXACTNESS
    // =====================================================================

    function testR2Rev_FotReclaim_ReceivedExact_InclExitFee_VaultZero() public {
        R2RevFotToken ft = new R2RevFotToken();
        SealEscrowVault v2 = new SealEscrowVault(address(ft), admin);
        SealFactoring f2 = new SealFactoring(address(v2), treasury, admin, settler, 20, 8_000);
        vm.prank(admin);
        v2.setSettlementModule(address(f2));
        ft.mint(buyer, 1_000_000);
        vm.prank(buyer);
        ft.approve(address(v2), type(uint256).max);

        bytes32 taskId = keccak256("r2rev-fot-reclaim");
        SealAuthorization memory seal = SealAuthorization({
            taskId: taskId, buyer: buyer, provider: worker, amount: 10_000,
            expectedSchemaHash: schemaHash, deadline: block.timestamp + 7 days, nonce: 0
        });
        _registerWith(v2, seal, buyerPk);
        v2.lockEscrow(taskId);
        // Entry fee: 10_000 sent, 9_000 received/accounted, 1_000 burned.
        assertEq(v2.lockedAmountOf(taskId), 9_000);
        assertEq(ft.balanceOf(address(v2)), 9_000);
        uint256 buyerAfterLock = ft.balanceOf(buyer);
        assertEq(buyerAfterLock, 990_000);

        vm.prank(buyer);
        uint256 reclaimed = f2.reclaimLockedEscrow(taskId);
        assertEq(reclaimed, 9_000, "reclaim amount = RECEIVED 9_000, never nominal 10_000");
        // Exit fee: vault sends 9_000, token burns 900, buyer credited 8_100.
        assertEq(ft.balanceOf(buyer) - buyerAfterLock, 8_100, "9_000 - 900 exit fee");
        assertEq(ft.balanceOf(address(v2)), 0, "vault left at exactly 0");
        assertEq(v2.activeLockedCount(), 0);
        // Conservation: 990_000 + 8_100 + 0 = 998_100; total burned = 1_900.
        assertEq(ft.totalSupply(), 1_000_000 - 1_900, "only the two token fees burned, nothing else lost/created");
    }

    function testR2Rev_FotReclaim_DustReceived_Exact() public {
        R2RevFotToken ft = new R2RevFotToken();
        SealEscrowVault v2 = new SealEscrowVault(address(ft), admin);
        SealFactoring f2 = new SealFactoring(address(v2), treasury, admin, settler, 20, 8_000);
        vm.prank(admin);
        v2.setSettlementModule(address(f2));
        ft.mint(buyer, 100_000);
        vm.prank(buyer);
        ft.approve(address(v2), type(uint256).max);
        bytes32 taskId = keccak256("r2rev-fot-dust");
        SealAuthorization memory seal = SealAuthorization({
            taskId: taskId, buyer: buyer, provider: worker, amount: 500,
            expectedSchemaHash: schemaHash, deadline: block.timestamp + 7 days, nonce: 0
        });
        _registerWith(v2, seal, buyerPk);
        v2.lockEscrow(taskId);
        assertEq(v2.lockedAmountOf(taskId), 450, "500 - 50 entry fee");
        uint256 buyerAfterLock = ft.balanceOf(buyer);
        vm.prank(buyer);
        uint256 reclaimed = f2.reclaimLockedEscrow(taskId);
        assertEq(reclaimed, 450);
        assertEq(ft.balanceOf(buyer) - buyerAfterLock, 405, "450 - 45 exit fee");
        assertEq(ft.balanceOf(address(v2)), 0);
        assertEq(v2.activeLockedCount(), 0);
    }
}
