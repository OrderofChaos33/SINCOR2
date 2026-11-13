// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {
    SealAuthorization,
    SealEscrowVault,
    SealFactoring
} from "../contracts/SealFactoring.sol";

contract ReviewToken is ERC20 {
    constructor() ERC20("Review USDC", "rUSDC") {}
    function mint(address to, uint256 amount) external { _mint(to, amount); }
}

/// @notice 10% fee-on-transfer token (same mechanics as the adversarial
///         suite's mock, renamed to avoid duplicate-definition clashes).
contract ReviewFotToken is ERC20 {
    constructor() ERC20("ReviewFoT", "rFOT") {}
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

/**
 * @notice INDEPENDENT FIX-PASS RE-REVIEW — Lane 4 SealFactoring.
 *         Written by the adversarial re-reviewer (not the fix author).
 *         Tests assert the SECURE post-fix behavior. A test that FAILS
 *         because the attack succeeded is a FINDING, flagged in its name.
 */
contract SealFactoringFixReviewTest is Test {
    ReviewToken internal token;
    SealEscrowVault internal vault;
    SealFactoring internal factoring;

    uint256 internal buyerPk = 0xB0B;
    uint256 internal buyer2Pk = 0xB0C;
    uint256 internal exWorkerPk = 0xD1; // an address that acts as worker, then as buyer
    address internal buyer;
    address internal buyer2;
    address internal worker = address(0x1002);
    address internal worker2 = address(0x100A);
    address internal treasury = address(0x1003);
    address internal admin = address(0x1004);
    address internal settler = address(0x1005);
    address internal settler2 = address(0x1015);
    address internal verifier2 = address(0x1016);
    address internal funder = address(0x1006);
    address internal payee1 = address(0x1007);
    address internal payee2 = address(0x1008);
    address internal payeeX = address(0x1009);
    address internal stranger = address(0x1010);
    address internal evilModule = address(0x666);

    uint256 internal constant GROSS = 1_000_000;
    uint256 internal constant LIQUIDITY = 4_000_000;
    bytes32 internal schemaHash = keccak256("task-spec-v1");

    function setUp() public {
        buyer = vm.addr(buyerPk);
        buyer2 = vm.addr(buyer2Pk);
        token = new ReviewToken();
        vault = new SealEscrowVault(address(token), admin);
        factoring = new SealFactoring(address(vault), treasury, admin, settler, 20, 8_000);
        vm.prank(admin);
        vault.setSettlementModule(address(factoring));
        vm.startPrank(admin);
        factoring.setPayeeAllowlist(payee1, true); // epoch 1
        factoring.setPayeeAllowlist(payee2, true); // epoch 2
        vm.stopPrank();

        token.mint(buyer, 10_000_000);
        token.mint(buyer2, 10_000_000);
        token.mint(funder, 10_000_000);
        token.mint(worker, 1_000_000);
        token.mint(vm.addr(exWorkerPk), 10_000_000);

        address[5] memory vaultApprovers = [buyer, buyer2, vm.addr(exWorkerPk), worker, worker2];
        for (uint256 i = 0; i < vaultApprovers.length; i++) {
            vm.prank(vaultApprovers[i]);
            token.approve(address(vault), type(uint256).max);
            vm.prank(vaultApprovers[i]);
            token.approve(address(factoring), type(uint256).max);
        }
        vm.prank(funder);
        token.approve(address(factoring), type(uint256).max);
        vm.prank(funder);
        factoring.fundLiquidity(LIQUIDITY);
    }

    // --- helpers ------------------------------------------------------------

    function _sealCustom(bytes32 taskId, address b, address p, uint256 amt, uint256 deadline, uint256 nonce)
        internal view returns (SealAuthorization memory)
    {
        return SealAuthorization({
            taskId: taskId, buyer: b, provider: p, amount: amt,
            expectedSchemaHash: schemaHash, deadline: deadline, nonce: nonce
        });
    }

    function _registerWith(SealEscrowVault v, SealAuthorization memory seal, uint256 pk) internal {
        (uint8 vv, bytes32 r, bytes32 s) = vm.sign(pk, v.hashSeal(seal));
        v.registerSeal(seal, vv, r, s);
    }

    /// @dev Register+lock on the main vault for an arbitrary buyer key / provider.
    function _lockFor(bytes32 taskId, uint256 bPk, address provider, uint256 amt)
        internal returns (SealAuthorization memory seal)
    {
        address b = vm.addr(bPk);
        seal = _sealCustom(taskId, b, provider, amt, block.timestamp + 7 days, vault.nonces(b));
        _registerWith(vault, seal, bPk);
        vault.lockEscrow(taskId);
    }

    function _lock(bytes32 taskId) internal returns (SealAuthorization memory) {
        return _lockFor(taskId, buyerPk, worker, GROSS);
    }

    function _attest(bytes32 taskId, bytes32 sigil) internal {
        vm.prank(admin); // verifier == admin by default
        factoring.attestVerification(taskId, sigil);
    }

    function _attestAndSettle(bytes32 taskId, bytes32 sigil) internal {
        _attest(taskId, sigil);
        vm.prank(settler);
        factoring.settle(taskId, sigil);
    }

    function _state(bytes32 taskId) internal view returns (SealFactoring.AdvanceState) {
        (SealFactoring.AdvanceState st,,,,,,) = factoring.advances(taskId);
        return st;
    }

    /// @dev Drive an advance to default via buyer dispute + window lapse,
    ///      booking clawback debt (spend has no bond behind it).
    function _defaultViaDispute(bytes32 taskId, uint256 advanceAmt, uint256 spendAmt) internal {
        factoring.issueAdvance(taskId, advanceAmt);
        if (spendAmt > 0) {
            (, address w,,,,,) = _advFull(taskId);
            vm.prank(w);
            factoring.spendAdvance(taskId, payee1, spendAmt);
        }
        (, , address b,,,,) = _advFull(taskId);
        vm.prank(b);
        factoring.markFailed(taskId);
        vm.warp(block.timestamp + factoring.DISPUTE_WINDOW() + 1);
        factoring.finalizeFailure(taskId);
    }

    function _advFull(bytes32 taskId)
        internal view returns (SealFactoring.AdvanceState st, address w, address b, uint256 g, uint256 c, uint256 s, bytes32 sig)
    {
        return factoring.advances(taskId);
    }

    // =====================================================================
    // (a) ROLE SEPARATION — collision games in both orders
    // =====================================================================

    function testReview_RoleCollision_SetVerifierToSettler_Reverts() public {
        vm.prank(admin);
        vm.expectRevert(SealFactoring.RolesMustBeSeparate.selector);
        factoring.setVerifier(settler);
        assertEq(factoring.verifier(), admin, "verifier unchanged");
    }

    function testReview_RoleCollision_SetSettlerToVerifier_Reverts() public {
        // verifier is `admin` at construction.
        vm.prank(admin);
        vm.expectRevert(SealFactoring.RolesMustBeSeparate.selector);
        factoring.setSettler(admin);
        assertEq(factoring.settler(), settler, "settler unchanged");
    }

    function testReview_RoleCollision_ChainedRotation_CannotManufactureCollision() public {
        // Rotate verifier -> verifier2; now try to make settler == verifier2.
        vm.prank(admin);
        factoring.setVerifier(verifier2);
        vm.prank(admin);
        vm.expectRevert(SealFactoring.RolesMustBeSeparate.selector);
        factoring.setSettler(verifier2);
        // Rotate settler -> settler2; now try to make verifier == settler2.
        vm.prank(admin);
        factoring.setSettler(settler2);
        vm.prank(admin);
        vm.expectRevert(SealFactoring.RolesMustBeSeparate.selector);
        factoring.setVerifier(settler2);
        // Zero-address and non-admin attempts also fail.
        vm.prank(admin);
        vm.expectRevert(SealFactoring.ZeroAddress.selector);
        factoring.setVerifier(address(0));
        vm.prank(stranger);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.setSettler(stranger);
        vm.prank(stranger);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.setVerifier(stranger);
        assertTrue(factoring.verifier() != factoring.settler(), "roles still separate after the chain");
    }

    /// @notice FINDING probe — FLIPPED by the round-2 fix: the constructor
    ///         previously set verifier = admin with NO RolesMustBeSeparate
    ///         check against the `_settler` parameter, so deploying with
    ///         settler == admin collapsed the two-key control to ONE key.
    ///         The scenario/setup is unchanged; the expectation is now
    ///         that the colliding deployment REVERTS at construction, so
    ///         the single key can never attest AND settle alone.
    function testReview_ConstructorRoleCollision_SingleKeyAttestsAndSettles_FINDING() public {
        SealEscrowVault v2 = new SealEscrowVault(address(token), admin);
        // settler := admin == the address that would become verifier.
        // Round-2 fix: the constructor applies RolesMustBeSeparate, so
        // this deployment — the entire attack — fails right here.
        vm.expectRevert(SealFactoring.RolesMustBeSeparate.selector);
        new SealFactoring(address(v2), treasury, admin, admin, 20, 8_000);
        // Separation still holds on the live (correctly deployed) system:
        assertTrue(factoring.settler() != factoring.verifier(),
            "role separation holds: constructor collision now reverts at deploy");
    }

    function testReview_Rotation_OldVerifierCannotAttest_OldSettlerCannotSettle() public {
        bytes32 taskId = keccak256("review-rotation");
        _lock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        vm.startPrank(admin);
        factoring.setVerifier(verifier2);
        factoring.setSettler(settler2);
        vm.stopPrank();
        // Old verifier (admin) can no longer attest.
        vm.prank(admin);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.attestVerification(taskId, keccak256("sigil"));
        // New verifier attests; OLD settler can no longer execute.
        vm.prank(verifier2);
        factoring.attestVerification(taskId, keccak256("sigil"));
        vm.prank(settler);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.settle(taskId, keccak256("sigil"));
        vm.prank(settler2);
        factoring.settle(taskId, keccak256("sigil"));
        assertEq(uint256(_state(taskId)), uint256(SealFactoring.AdvanceState.Settled));
    }

    /// @notice Residual-behavior probe (documented, not counted as a hole):
    ///         an attestation made by a verifier BEFORE rotation remains
    ///         settleable after that verifier is rotated out — rotation does
    ///         not revoke in-flight attestations.
    function testReview_AttestationSurvivesVerifierRotation_Residual() public {
        bytes32 taskId = keccak256("review-attest-rotation");
        _lock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        _attest(taskId, keccak256("sigil")); // by admin-as-verifier
        vm.prank(admin);
        factoring.setVerifier(verifier2); // rotate the attester out
        vm.prank(settler);
        factoring.settle(taskId, keccak256("sigil")); // still settles on the old attestation
        assertEq(uint256(_state(taskId)), uint256(SealFactoring.AdvanceState.Settled));
    }

    // =====================================================================
    // (a) ATTESTATION — overwrite races, cross-task reuse
    // =====================================================================

    function testReview_AttestOverwrite_LatestWins_StaleCommitmentCannotSettle() public {
        bytes32 taskId = keccak256("review-attest-overwrite");
        _lock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        _attest(taskId, keccak256("commitment-1"));
        _attest(taskId, keccak256("commitment-2")); // verifier re-attests (overwrites)
        assertEq(factoring.verifiedCommitment(taskId), keccak256("commitment-2"));
        vm.prank(settler);
        vm.expectRevert(SealFactoring.VerificationRequired.selector);
        factoring.settle(taskId, keccak256("commitment-1")); // stale attestation useless
        vm.prank(settler);
        factoring.settle(taskId, keccak256("commitment-2"));
        assertEq(uint256(_state(taskId)), uint256(SealFactoring.AdvanceState.Settled));
    }

    function testReview_AttestCrossTaskReuse_Reverts() public {
        bytes32 taskA = keccak256("review-x-task-a");
        bytes32 taskB = keccak256("review-x-task-b");
        _lock(taskA);
        _lock(taskB);
        factoring.issueAdvance(taskA, 100_000);
        factoring.issueAdvance(taskB, 100_000);
        bytes32 shared = keccak256("shared-commitment");
        _attest(taskA, shared); // attested for A ONLY
        vm.prank(settler);
        vm.expectRevert(SealFactoring.VerificationRequired.selector);
        factoring.settle(taskB, shared); // cannot ride on A's attestation
        // B settles only once B itself is attested (same value is fine —
        // the binding is per-task, not global uniqueness).
        _attest(taskB, shared);
        vm.prank(settler);
        factoring.settle(taskB, shared);
        assertEq(uint256(_state(taskB)), uint256(SealFactoring.AdvanceState.Settled));
    }

    function testReview_AttestGuards_NonVerifier_Zero_NonActive() public {
        bytes32 taskId = keccak256("review-attest-guards");
        _lock(taskId);
        // No advance yet -> AdvanceNotActive even for the verifier.
        vm.prank(admin);
        vm.expectRevert(SealFactoring.AdvanceNotActive.selector);
        factoring.attestVerification(taskId, keccak256("sigil"));
        factoring.issueAdvance(taskId, 100_000);
        // The settler is NOT the verifier: cannot attest.
        vm.prank(settler);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.attestVerification(taskId, keccak256("sigil"));
        // Zero commitment rejected.
        vm.prank(admin);
        vm.expectRevert(SealFactoring.SigilCommitmentZero.selector);
        factoring.attestVerification(taskId, bytes32(0));
        // After settlement, re-attestation is impossible.
        _attestAndSettle(taskId, keccak256("sigil"));
        vm.prank(admin);
        vm.expectRevert(SealFactoring.AdvanceNotActive.selector);
        factoring.attestVerification(taskId, keccak256("sigil-2"));
    }

    // =====================================================================
    // (b) VAULT REPOINT — timelock games + activeLockedCount on every exit
    // =====================================================================

    function testReview_Timelock_ExecuteEarlyAndCancel() public {
        vm.prank(admin);
        vault.setSettlementModule(evilModule);
        uint256 effAt = vault.pendingModuleEffectiveAt();
        uint256 nowTs = block.timestamp;
        vm.prank(admin);
        vm.expectRevert(
            abi.encodeWithSelector(SealEscrowVault.ModuleChangeNotReady.selector, effAt, nowTs)
        );
        vault.executeSettlementModuleChange();
        assertEq(vault.settlementModule(), address(factoring), "module unchanged during delay");
        // Cancel kills the proposal entirely.
        vm.prank(admin);
        vault.cancelSettlementModuleChange();
        assertEq(vault.pendingSettlementModule(), address(0));
        vm.warp(block.timestamp + vault.MODULE_CHANGE_DELAY() + 1);
        vm.prank(admin);
        vm.expectRevert(SealEscrowVault.NoPendingModuleChange.selector);
        vault.executeSettlementModuleChange();
        assertEq(vault.settlementModule(), address(factoring));
        // Non-admin cannot execute or cancel.
        vm.prank(admin);
        vault.setSettlementModule(evilModule);
        vm.prank(stranger);
        vm.expectRevert(SealEscrowVault.Unauthorized.selector);
        vault.executeSettlementModuleChange();
        vm.prank(stranger);
        vm.expectRevert(SealEscrowVault.Unauthorized.selector);
        vault.cancelSettlementModuleChange();
    }

    function testReview_Timelock_ReproposeResetsClock() public {
        address moduleB = address(0x777);
        vm.prank(admin);
        vault.setSettlementModule(evilModule);
        uint256 firstEffective = vault.pendingModuleEffectiveAt();
        vm.warp(block.timestamp + 1 days); // halfway through the first delay
        vm.prank(admin);
        vault.setSettlementModule(moduleB); // re-propose: clock RESETS
        uint256 secondEffective = vault.pendingModuleEffectiveAt();
        assertGt(secondEffective, firstEffective, "re-proposal pushed the effective time out");
        assertEq(vault.pendingSettlementModule(), moduleB, "first proposal superseded");
        // At the ORIGINAL effective time, execution is still too early.
        vm.warp(firstEffective);
        vm.prank(admin);
        vm.expectRevert(
            abi.encodeWithSelector(
                SealEscrowVault.ModuleChangeNotReady.selector, secondEffective, firstEffective)
        );
        vault.executeSettlementModuleChange();
        vm.warp(secondEffective);
        vm.prank(admin);
        vault.executeSettlementModuleChange();
        assertEq(vault.settlementModule(), moduleB, "only the re-proposed module lands, on its own clock");
    }

    function testReview_ActiveLockedCount_SettleExitDecrements() public {
        bytes32 t1 = keccak256("review-count-settle-1");
        bytes32 t2 = keccak256("review-count-settle-2");
        _lock(t1);
        _lock(t2);
        assertEq(vault.activeLockedCount(), 2);
        factoring.issueAdvance(t1, 100_000);
        _attestAndSettle(t1, keccak256("sigil"));
        assertEq(vault.activeLockedCount(), 1, "settle decremented exactly once");
        factoring.issueAdvance(t2, 100_000);
        _attestAndSettle(t2, keccak256("sigil"));
        assertEq(vault.activeLockedCount(), 0, "second settle drained the count");
    }

    function testReview_ActiveLockedCount_BothFailureExitsDecrement() public {
        bytes32 t1 = keccak256("review-count-finalize");
        bytes32 t2 = keccak256("review-count-adjudicate");
        _lock(t1);
        _lock(t2);
        assertEq(vault.activeLockedCount(), 2);
        // Exit 1: buyer dispute -> window lapse -> permissionless finalize.
        factoring.issueAdvance(t1, 100_000);
        vm.prank(buyer);
        factoring.markFailed(t1);
        vm.warp(block.timestamp + factoring.DISPUTE_WINDOW());
        factoring.finalizeFailure(t1);
        assertEq(vault.activeLockedCount(), 1, "finalizeFailure decremented");
        // Exit 2: buyer dispute -> settler adjudication.
        factoring.issueAdvance(t2, 100_000);
        vm.prank(buyer);
        factoring.markFailed(t2);
        vm.prank(settler);
        factoring.markFailed(t2);
        assertEq(vault.activeLockedCount(), 0, "settler-adjudicated failure decremented");
    }

    function testReview_ActiveLockedCount_ExpireExit_AndRepointUnblocksAtZero() public {
        bytes32 taskId = keccak256("review-count-expire");
        SealAuthorization memory seal = _lock(taskId);
        assertEq(vault.activeLockedCount(), 1);
        factoring.issueAdvance(taskId, 100_000);
        // Propose a repoint; even after the delay it is blocked at count 1.
        vm.prank(admin);
        vault.setSettlementModule(evilModule);
        vm.warp(block.timestamp + vault.MODULE_CHANGE_DELAY() + 1);
        vm.prank(admin);
        vm.expectRevert(
            abi.encodeWithSelector(SealEscrowVault.ModuleChangeBlockedByActiveEscrow.selector, 1)
        );
        vault.executeSettlementModuleChange();
        // Expire (now past the 7-day seal deadline too) decrements to zero.
        vm.warp(seal.deadline + 1);
        factoring.expire(taskId);
        assertEq(vault.activeLockedCount(), 0, "expire decremented");
        vm.prank(admin);
        vault.executeSettlementModuleChange();
        assertEq(vault.settlementModule(), evilModule, "repoint lands once nothing is locked");
    }

    /// @notice FINDING probe — FLIPPED by the round-2 fix: a locked escrow
    ///         with NO advance previously had exits only through an Active
    ///         advance, so a seal whose gross could never satisfy
    ///         MIN_ADVANCE vs the max-advance cap (gross 500: max advance
    ///         380 < MIN_ADVANCE 1_000) stranded the buyer's funds forever
    ///         and DoSed the vault module repoint. The scenario/setup is
    ///         unchanged; the expectation is now that the buyer RECLAIMS
    ///         the no-advance lock (round-2 `reclaimLockedEscrow`), the
    ///         count returns to 0, and the repoint then executes.
    function testReview_DustLockNoAdvance_PermanentlyBlocksRepoint_FINDING() public {
        bytes32 taskId = keccak256("review-dust-lock");
        _lockFor(taskId, buyerPk, worker, 500); // gross 500, received 500
        assertEq(vault.activeLockedCount(), 1);
        uint256 buyerAfterLock = token.balanceOf(buyer);
        // No advance can ever be issued against this lock:
        vm.expectRevert(
            abi.encodeWithSelector(SealFactoring.AdvanceBelowMinimum.selector, 999, 1_000)
        );
        factoring.issueAdvance(taskId, 999);
        vm.expectRevert(
            abi.encodeWithSelector(SealFactoring.AdvanceExceedsMax.selector, 1_000, 380)
        );
        factoring.issueAdvance(taskId, 1_000);
        // Round-2 fix: with NO advance in existence, the buyer reclaims
        // the locked escrow in full and the count returns to zero.
        vm.prank(buyer);
        factoring.reclaimLockedEscrow(taskId);
        assertEq(token.balanceOf(buyer) - buyerAfterLock, 500, "buyer reclaimed the full locked 500");
        assertEq(token.balanceOf(address(vault)), 0, "vault drained by the reclaim");
        assertFalse(vault.isLocked(taskId), "lock terminally closed by the reclaim");
        assertEq(vault.activeLockedCount(), 0, "count returned to 0 via the reclaim");
        // Admin rotates the module (e.g. away from a compromised
        // factoring contract). Timelock passes; nothing blocks it now.
        vm.prank(admin);
        vault.setSettlementModule(evilModule);
        vm.warp(block.timestamp + vault.MODULE_CHANGE_DELAY() + 365 days);
        vm.prank(admin);
        vault.executeSettlementModuleChange(); // succeeds post-timelock at count 0
        assertEq(vault.settlementModule(), evilModule,
            "dust lock reclaimed: repoint executes once the count reaches 0");
    }

    // =====================================================================
    // (c) SELF-DEALING + DEBT GATE
    // =====================================================================

    function testReview_SelfDealing_DirectBuyerEqualsProvider_Reverts() public {
        uint256 selfPk = 0xCAFE;
        address self = vm.addr(selfPk);
        token.mint(self, 2_000_000);
        vm.startPrank(self);
        token.approve(address(vault), type(uint256).max);
        token.approve(address(factoring), type(uint256).max);
        vm.stopPrank();
        bytes32 taskId = keccak256("review-selfdeal");
        SealAuthorization memory seal =
            _sealCustom(taskId, self, self, GROSS, block.timestamp + 7 days, vault.nonces(self));
        _registerWith(vault, seal, selfPk);
        vault.lockEscrow(taskId);
        vm.expectRevert(SealFactoring.SelfDealingBlocked.selector);
        factoring.issueAdvance(taskId, 100_000);
    }

    function testReview_DebtGate_FreshBuyer_IndebtedProvider_Blocked() public {
        // Task 1: worker defaults via the dispute path, booking debt.
        bytes32 t1 = keccak256("review-debt-p-1");
        _lock(t1);
        _defaultViaDispute(t1, 100_000, 100_000);
        uint256 debt = factoring.clawbackDebtOf(worker);
        assertEq(debt, 100_200);
        // Task 2: a FRESH buyer (never defaulted) names the indebted worker.
        bytes32 t2 = keccak256("review-debt-p-2");
        _lockFor(t2, buyer2Pk, worker, GROSS);
        vm.expectRevert(
            abi.encodeWithSelector(SealFactoring.OutstandingClawbackDebt.selector, worker, debt)
        );
        factoring.issueAdvance(t2, 100_000);
    }

    function testReview_DebtGate_ExpireCreatedDebt_BlocksFreshBuyer() public {
        // Task 1: debt created by the EXPIRE path specifically.
        bytes32 t1 = keccak256("review-debt-e-1");
        SealAuthorization memory seal = _lock(t1);
        factoring.issueAdvance(t1, 50_000);
        vm.prank(worker);
        factoring.spendAdvance(t1, payee1, 50_000);
        vm.warp(seal.deadline + 1);
        factoring.expire(t1);
        uint256 debt = factoring.clawbackDebtOf(worker);
        assertEq(debt, 50_100, "expire booked 50k + 100 fee");
        // Task 2: fresh buyer, same provider -> still gated.
        bytes32 t2 = keccak256("review-debt-e-2");
        _lockFor(t2, buyer2Pk, worker, GROSS);
        vm.expectRevert(
            abi.encodeWithSelector(SealFactoring.OutstandingClawbackDebt.selector, worker, debt)
        );
        factoring.issueAdvance(t2, 50_000);
    }

    function testReview_DebtGate_IndebtedBuyerRole_Blocked() public {
        // Address X works task 1 (as provider) and defaults, booking debt.
        address x = vm.addr(exWorkerPk);
        bytes32 t1 = keccak256("review-debt-b-1");
        _lockFor(t1, buyerPk, x, GROSS);
        _defaultViaDispute(t1, 80_000, 80_000);
        uint256 debt = factoring.clawbackDebtOf(x);
        assertGt(debt, 0);
        // Task 2: X returns as the BUYER, with a clean provider. The buyer-
        // side gate must catch the role switch.
        bytes32 t2 = keccak256("review-debt-b-2");
        _lockFor(t2, exWorkerPk, worker2, GROSS);
        vm.expectRevert(
            abi.encodeWithSelector(SealFactoring.OutstandingClawbackDebt.selector, x, debt)
        );
        factoring.issueAdvance(t2, 50_000);
    }

    // =====================================================================
    // (d) ALLOWLIST EPOCHS — remove/re-add cycles across issuances
    // =====================================================================

    function testReview_AllowlistEpoch_RemoveReaddCycles_PerIssuanceSnapshot() public {
        // payeeX: allow (epoch 3) -> remove (epoch 4)
        vm.startPrank(admin);
        factoring.setPayeeAllowlist(payeeX, true);
        factoring.setPayeeAllowlist(payeeX, false);
        vm.stopPrank();
        bytes32 tA = keccak256("review-epoch-a");
        _lock(tA);
        factoring.issueAdvance(tA, 200_000); // snapshot: epoch 4 (payeeX = false)
        vm.prank(worker);
        vm.expectRevert(abi.encodeWithSelector(SealFactoring.PayeeNotAllowlisted.selector, payeeX));
        factoring.spendAdvance(tA, payeeX, 10_000);

        // Re-add (epoch 5); a NEW advance sees payeeX as allowed...
        vm.prank(admin);
        factoring.setPayeeAllowlist(payeeX, true);
        bytes32 tB = keccak256("review-epoch-b");
        _lock(tB);
        factoring.issueAdvance(tB, 200_000); // snapshot: epoch 5 (payeeX = true)
        vm.prank(worker);
        factoring.spendAdvance(tB, payeeX, 10_000);
        assertEq(token.balanceOf(payeeX), 10_000);

        // Remove again (epoch 6): tB (snapshot 5) STILL spends to payeeX,
        // while a fresh advance (snapshot 6) cannot.
        vm.prank(admin);
        factoring.setPayeeAllowlist(payeeX, false);
        vm.prank(worker);
        factoring.spendAdvance(tB, payeeX, 10_000);
        assertEq(token.balanceOf(payeeX), 20_000, "issuance snapshot survives later removal");
        bytes32 tC = keccak256("review-epoch-c");
        _lock(tC);
        factoring.issueAdvance(tC, 200_000); // snapshot: epoch 6 (payeeX = false)
        vm.prank(worker);
        vm.expectRevert(abi.encodeWithSelector(SealFactoring.PayeeNotAllowlisted.selector, payeeX));
        factoring.spendAdvance(tC, payeeX, 10_000);
    }

    function testReview_AllowlistEpoch_UnrelatedChurnDoesNotDisturbSnapshot() public {
        bytes32 taskId = keccak256("review-epoch-churn");
        _lock(taskId);
        factoring.issueAdvance(taskId, 200_000); // snapshot epoch 2 (payee1+payee2 allowed)
        // Admin churns OTHER payees, driving the global epoch far forward.
        vm.startPrank(admin);
        factoring.setPayeeAllowlist(payeeX, true);
        factoring.setPayeeAllowlist(payeeX, false);
        factoring.setPayeeAllowlist(payee2, false);
        factoring.setPayeeAllowlist(payee2, true);
        vm.stopPrank();
        assertGt(factoring.allowlistEpoch(), factoring.advanceAllowlistEpoch(taskId));
        // Snapshot resolution for THIS advance is untouched by the churn:
        vm.startPrank(worker);
        factoring.spendAdvance(taskId, payee1, 10_000);
        factoring.spendAdvance(taskId, payee2, 10_000);
        vm.stopPrank();
        assertEq(token.balanceOf(payee1), 10_000);
        assertEq(token.balanceOf(payee2), 10_000);
    }

    // =====================================================================
    // (e) FEE SNAPSHOT + (i) PLATFORM-FEE CEIL
    // =====================================================================

    function testReview_FeeSnapshot_FailurePathIgnoresDoubleHike() public {
        bytes32 taskId = keccak256("review-fee-snap-fail");
        _lock(taskId);
        factoring.issueAdvance(taskId, 200_000);
        assertEq(factoring.advanceFeeBpsSnapshot(taskId), 20);
        vm.prank(worker);
        factoring.spendAdvance(taskId, payee1, 100_000);
        vm.startPrank(admin);
        factoring.setFactoringFeeBps(500);
        factoring.setFactoringFeeBps(300); // second mid-flight change
        vm.stopPrank();
        vm.prank(buyer);
        factoring.markFailed(taskId);
        vm.warp(block.timestamp + factoring.DISPUTE_WINDOW());
        factoring.finalizeFailure(taskId);
        // Owed at the 20 bps snapshot: 100_000 + 200, not 300/500 bps math.
        assertEq(factoring.clawbackDebtOf(worker), 100_200);
    }

    function testReview_PlatformFeeCeil_ExactDivisibleNotOvercharged_NonDivisibleCeils() public {
        // Gross 20_000: 5% is exactly 1_000 — ceil must NOT overcharge.
        bytes32 t1 = keccak256("review-ceil-exact");
        _lockFor(t1, buyerPk, worker, 20_000);
        factoring.issueAdvance(t1, 1_000);
        uint256 treasuryBefore = token.balanceOf(treasury);
        uint256 workerBefore = token.balanceOf(worker);
        _attestAndSettle(t1, keccak256("sigil"));
        assertEq(token.balanceOf(treasury) - treasuryBefore, 1_000, "exact fee, no ceil overcharge");
        assertEq(token.balanceOf(worker) - workerBefore, 19_000);
        // Gross 20_001: floor would be 1_000, ceil is 1_001.
        bytes32 t2 = keccak256("review-ceil-up");
        _lockFor(t2, buyerPk, worker, 20_001);
        factoring.issueAdvance(t2, 1_000);
        treasuryBefore = token.balanceOf(treasury);
        workerBefore = token.balanceOf(worker);
        _attestAndSettle(t2, keccak256("sigil"));
        assertEq(token.balanceOf(treasury) - treasuryBefore, 1_001, "fee ceils up by 1 wei");
        assertEq(token.balanceOf(worker) - workerBefore, 19_000, "20_001 - 1_001 exactly");
    }

    // =====================================================================
    // (f) DISPUTE GAMES
    // =====================================================================

    function testReview_Dispute_OnSettledAdvance_AndFinalizeWithoutDispute_Revert() public {
        bytes32 t1 = keccak256("review-dispute-settled");
        _lock(t1);
        factoring.issueAdvance(t1, 100_000);
        _attestAndSettle(t1, keccak256("sigil"));
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.AdvanceNotActive.selector);
        factoring.markFailed(t1); // dispute on a settled advance: impossible
        vm.expectRevert(SealFactoring.AdvanceNotActive.selector);
        factoring.finalizeFailure(t1);

        bytes32 t2 = keccak256("review-dispute-none");
        _lock(t2);
        factoring.issueAdvance(t2, 100_000);
        vm.expectRevert(SealFactoring.NoFailureDispute.selector);
        factoring.finalizeFailure(t2); // no dispute was ever opened
    }

    function testReview_Dispute_FinalizeAtExactWindowEnd_OneSecondEarlyReverts() public {
        bytes32 taskId = keccak256("review-dispute-timing");
        _lock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        vm.prank(buyer);
        factoring.markFailed(taskId);
        uint256 finalizeAt = factoring.failureDisputeOpenedAt(taskId) + factoring.DISPUTE_WINDOW();
        vm.warp(finalizeAt - 1);
        vm.expectRevert(
            abi.encodeWithSelector(SealFactoring.DisputeWindowStillOpen.selector, finalizeAt)
        );
        factoring.finalizeFailure(taskId);
        assertEq(uint256(_state(taskId)), uint256(SealFactoring.AdvanceState.Active), "still contestable");
        vm.warp(finalizeAt); // exactly at the boundary: finalizable
        vm.prank(stranger); // permissionless
        factoring.finalizeFailure(taskId);
        assertEq(uint256(_state(taskId)), uint256(SealFactoring.AdvanceState.Defaulted));
    }

    function testReview_Dispute_SettleContestClearsDisputeState() public {
        bytes32 taskId = keccak256("review-dispute-contest");
        _lock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        vm.prank(buyer);
        factoring.markFailed(taskId);
        assertTrue(factoring.failureDisputed(taskId));
        _attestAndSettle(taskId, keccak256("sigil")); // settler contests in-window
        assertFalse(factoring.failureDisputed(taskId), "settlement cleared the dispute flag");
        assertEq(factoring.failureDisputeOpenedAt(taskId), factoring.failureDisputeOpenedAt(taskId)); // read sanity
        vm.warp(block.timestamp + factoring.DISPUTE_WINDOW() + 1);
        vm.expectRevert(SealFactoring.AdvanceNotActive.selector);
        factoring.finalizeFailure(taskId); // nothing left to finalize
    }

    function testReview_DisputeSpam_AndSettlerDeadlineRule() public {
        bytes32 t1 = keccak256("review-dispute-spam");
        _lock(t1);
        factoring.issueAdvance(t1, 100_000);
        vm.prank(buyer);
        factoring.markFailed(t1);
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.FailureAlreadyDisputed.selector);
        factoring.markFailed(t1); // spam: second dispute reverts
        // Settler CAN adjudicate the open dispute pre-deadline.
        vm.prank(settler);
        factoring.markFailed(t1);
        assertEq(uint256(_state(t1)), uint256(SealFactoring.AdvanceState.Defaulted));

        // Settler with NO dispute: pre-deadline reverts, post-deadline works.
        bytes32 t2 = keccak256("review-settler-deadline");
        SealAuthorization memory seal = _lock(t2);
        factoring.issueAdvance(t2, 100_000);
        vm.prank(settler);
        vm.expectRevert(SealFactoring.FailureDisputeRequired.selector);
        factoring.markFailed(t2);
        vm.warp(seal.deadline + 1);
        vm.prank(settler);
        factoring.markFailed(t2);
        assertEq(uint256(_state(t2)), uint256(SealFactoring.AdvanceState.Defaulted));
    }

    // =====================================================================
    // (g) DEADLINE EXACT-SECOND + (h) MIN_ADVANCE
    // =====================================================================

    function testReview_DeadlineExact_IssueSpendSettleAtDeadline_ExpireReverts() public {
        uint256 deadline = block.timestamp + 100;
        bytes32 tA = keccak256("review-dl-a");
        bytes32 tB = keccak256("review-dl-b");
        bytes32 tC = keccak256("review-dl-c");
        bytes32[3] memory tasks = [tA, tB, tC];
        for (uint256 i = 0; i < 3; i++) {
            SealAuthorization memory seal =
                _sealCustom(tasks[i], buyer, worker, GROSS, deadline, vault.nonces(buyer));
            _registerWith(vault, seal, buyerPk);
            vault.lockEscrow(tasks[i]);
        }
        factoring.issueAdvance(tC, 100_000); // C advanced early for the +1s probes
        factoring.issueAdvance(tB, 100_000); // B advanced so expire() reaches its deadline check
        vm.warp(deadline); // exactly the deadline second
        factoring.issueAdvance(tA, 100_000); // issue AT deadline: valid
        vm.prank(worker);
        factoring.spendAdvance(tA, payee1, 10_000); // spend AT deadline: valid
        _attestAndSettle(tA, keccak256("sigil")); // settle AT deadline: valid
        assertEq(uint256(_state(tA)), uint256(SealFactoring.AdvanceState.Settled));
        vm.expectRevert(SealFactoring.TaskNotYetExpired.selector);
        factoring.expire(tB); // expire AT deadline: still shut

        vm.warp(deadline + 1);
        vm.prank(worker);
        vm.expectRevert(SealFactoring.SealExpired.selector);
        factoring.spendAdvance(tC, payee1, 10_000); // spend at deadline+1: shut
        _attest(tC, keccak256("sigil"));
        vm.prank(settler);
        vm.expectRevert(SealFactoring.SealExpired.selector);
        factoring.settle(tC, keccak256("sigil")); // settle at deadline+1: shut
        factoring.expire(tC); // expire at deadline+1: open
        assertEq(uint256(_state(tC)), uint256(SealFactoring.AdvanceState.Defaulted));
    }

    function testReview_MinAdvance_ExactlyMinimumSucceeds_OneBelowReverts() public {
        bytes32 taskId = keccak256("review-min-adv");
        _lockFor(taskId, buyerPk, worker, 2_000); // max advance = (2000-100)*80% = 1520
        vm.expectRevert(
            abi.encodeWithSelector(SealFactoring.AdvanceBelowMinimum.selector, 999, 1_000)
        );
        factoring.issueAdvance(taskId, 999);
        factoring.issueAdvance(taskId, 1_000); // exactly MIN_ADVANCE
        (,,, , uint256 credit,,) = factoring.advances(taskId);
        assertEq(credit, 1_000);
        assertEq(factoring.MIN_ADVANCE(), 1_000);
    }

    // =====================================================================
    // (j) FoT LOCK — balance-delta accounting near MIN_ADVANCE
    // =====================================================================

    function testReview_FotLock_NearMinAdvance_ReceivedAccountingAndExits() public {
        ReviewFotToken ft = new ReviewFotToken();
        SealEscrowVault v2 = new SealEscrowVault(address(ft), admin);
        SealFactoring f2 = new SealFactoring(address(v2), treasury, admin, settler, 20, 8_000);
        vm.prank(admin);
        v2.setSettlementModule(address(f2));
        vm.prank(admin);
        f2.setPayeeAllowlist(payee1, true);
        ft.mint(buyer, 1_000_000);
        ft.mint(funder, 5_000_000);
        vm.prank(buyer);
        ft.approve(address(v2), type(uint256).max);
        vm.prank(funder);
        ft.approve(address(f2), type(uint256).max);
        vm.prank(funder);
        f2.fundLiquidity(2_000_000);

        bytes32 taskId = keccak256("review-fot-min");
        SealAuthorization memory seal =
            _sealCustom(taskId, buyer, worker, 2_000, block.timestamp + 7 days, 0);
        _registerWith(v2, seal, buyerPk);
        v2.lockEscrow(taskId);
        // 10% burned in transit: exactly 1_800 received and ACCOUNTED.
        assertEq(ft.balanceOf(address(v2)), 1_800);
        assertEq(v2.lockedAmountOf(taskId), 1_800, "collateral = received delta, not nominal 2_000");
        assertEq(v2.activeLockedCount(), 1);
        assertEq(f2.maxAdvanceFor(1_800), 1_368, "(1800 - 90 fee) * 80%");
        // Dust is still blocked even against FoT collateral...
        vm.expectRevert(
            abi.encodeWithSelector(SealFactoring.AdvanceBelowMinimum.selector, 999, 1_000)
        );
        f2.issueAdvance(taskId, 999);
        // ...and exactly MIN_ADVANCE issues against the received amount.
        f2.issueAdvance(taskId, 1_000);
        vm.prank(worker);
        f2.spendAdvance(taskId, payee1, 500);
        assertEq(ft.balanceOf(payee1), 450, "spend delivers 500 - 10% token fee (documented residual)");

        vm.prank(admin);
        f2.attestVerification(taskId, keccak256("sigil"));
        uint256 workerBefore = ft.balanceOf(worker);
        uint256 treasuryBefore = ft.balanceOf(treasury);
        vm.prank(settler);
        f2.settle(taskId, keccak256("sigil"));
        // Waterfall on received gross 1_800: platform fee ceil = 90,
        // factoring fee on 500 @20bps = 1, remainder = 1_800-90-500-1 = 1_209.
        // Recipients see those amounts minus the token's own 10% transfer fee.
        assertEq(ft.balanceOf(treasury) - treasuryBefore, 81, "90 - 9 token fee");
        assertEq(ft.balanceOf(worker) - workerBefore, 1_209 - 120, "1_209 - 120 token fee (floor)");
        assertEq(ft.balanceOf(address(v2)), 0, "vault fully drained, no phantom-amount wedge");
        assertEq(v2.activeLockedCount(), 0, "count decremented on the FoT settle exit");
        (SealFactoring.AdvanceState st,,,,,,) = f2.advances(taskId);
        assertEq(uint256(st), uint256(SealFactoring.AdvanceState.Settled));
    }
}
