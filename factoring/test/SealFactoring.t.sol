// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {
    SealAuthorization,
    SealEscrowVault,
    SealFactoring
} from "../contracts/SealFactoring.sol";

contract MockToken is ERC20 {
    constructor() ERC20("Mock USDC", "mUSDC") {}
    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

/**
 * @notice Full-loop + negative tests for Lane-4 SealFactoring.
 *         Money numbers are chosen so every division is exact:
 *           GROSS = 1_000_000; platform fee 5% = 50_000; net pool = 950_000;
 *           advanceBps 8_000 -> max advance = 760_000;
 *           factoring fee 20 bps (0.2%, the PROPOSED value — founder
 *           pricing decision, exercised here, not ratified).
 */
contract SealFactoringTest is Test {
    MockToken internal token;
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
    address internal payee2 = address(0x1008);
    address internal roguePayee = address(0x1009);
    address internal stranger = address(0x1010);

    uint256 internal constant GROSS = 1_000_000;
    uint256 internal constant LIQUIDITY = 2_000_000;
    uint256 internal constant FACTORING_FEE_BPS = 20; // 0.2% proposal
    uint256 internal constant ADVANCE_BPS = 8_000;

    bytes32 internal schemaHash = keccak256("task-spec-v1");

    function setUp() public {
        buyer = vm.addr(buyerPk);
        token = new MockToken();
        vault = new SealEscrowVault(address(token), admin);
        factoring = new SealFactoring(
            address(vault), treasury, admin, settler, FACTORING_FEE_BPS, ADVANCE_BPS
        );
        vm.prank(admin);
        vault.setSettlementModule(address(factoring));
        vm.startPrank(admin);
        factoring.setPayeeAllowlist(payee1, true);
        factoring.setPayeeAllowlist(payee2, true);
        vm.stopPrank();

        token.mint(buyer, 5_000_000);
        token.mint(funder, 5_000_000);
        token.mint(worker, 1_000_000);

        vm.prank(buyer);
        token.approve(address(vault), type(uint256).max);
        vm.prank(funder);
        token.approve(address(factoring), type(uint256).max);
        vm.prank(worker);
        token.approve(address(factoring), type(uint256).max);

        vm.prank(funder);
        factoring.fundLiquidity(LIQUIDITY);
    }

    // --- helpers ------------------------------------------------------------

    function _seal(bytes32 taskId, uint256 nonce) internal view returns (SealAuthorization memory) {
        return SealAuthorization({
            taskId: taskId,
            buyer: buyer,
            provider: worker,
            amount: GROSS,
            expectedSchemaHash: schemaHash,
            deadline: block.timestamp + 7 days,
            nonce: nonce
        });
    }

    function _register(bytes32 taskId) internal returns (SealAuthorization memory seal) {
        seal = _seal(taskId, vault.nonces(buyer));
        bytes32 digest = vault.hashSeal(seal);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(buyerPk, digest);
        vault.registerSeal(seal, v, r, s);
    }

    function _registerAndLock(bytes32 taskId) internal returns (SealAuthorization memory seal) {
        seal = _register(taskId);
        vault.lockEscrow(taskId);
    }

    /// @notice FIX-PASS helper: settlement now requires the verifier's
    ///         on-chain attestation (role separation — verifier defaults to
    ///         `admin`, distinct from `settler`) before the settler executes.
    function _attest(bytes32 taskId, bytes32 sigil) internal {
        vm.prank(admin);
        factoring.attestVerification(taskId, sigil);
    }

    function _attestAndSettle(bytes32 taskId, bytes32 sigil) internal {
        _attest(taskId, sigil);
        vm.prank(settler);
        factoring.settle(taskId, sigil);
    }

    // --- the full loop --------------------------------------------------------

    function testFullLoop_LockAdvanceSpendSettle_ExactMath() public {
        bytes32 taskId = keccak256("task-full-loop");
        _registerAndLock(taskId);
        assertTrue(vault.isLocked(taskId), "escrow must be locked");
        assertEq(token.balanceOf(address(vault)), GROSS);

        factoring.issueAdvance(taskId, 500_000);
        assertEq(factoring.totalOutstandingCredit(), 500_000);
        assertEq(factoring.maxAdvanceFor(GROSS), 760_000);

        vm.startPrank(worker);
        factoring.spendAdvance(taskId, payee1, 250_000);
        factoring.spendAdvance(taskId, payee2, 150_000);
        vm.stopPrank();
        assertEq(token.balanceOf(payee1), 250_000);
        assertEq(token.balanceOf(payee2), 150_000);

        uint256 workerBefore = token.balanceOf(worker);
        bytes32 sigil = keccak256(abi.encodePacked(taskId, "canonical-output"));
        _attestAndSettle(taskId, sigil);

        // Waterfall: platformFee 50_000 (5% canon, untouched);
        // spent 400_000 recovered; factoring fee = 400_000 * 20 / 10_000 = 800;
        // worker remainder = 1_000_000 - 50_000 - 400_000 - 800 = 549_200.
        assertEq(token.balanceOf(treasury), 50_000, "platform fee must be exactly 5%");
        assertEq(token.balanceOf(worker), workerBefore + 549_200, "worker remainder exact");
        // Factor: liquidity back + fee profit only. 2_000_000 - 400_000 spent
        // + 1_000_000 gross - 50_000 fee - 549_200 remainder = 2_000_800.
        assertEq(token.balanceOf(address(factoring)), LIQUIDITY + 800, "factor keeps principal + fee");
        assertEq(token.balanceOf(address(vault)), 0, "vault drained at settlement");
        assertEq(factoring.totalOutstandingCredit(), 0, "unspent credit cancelled");
        assertEq(factoring.PLATFORM_FEE_BPS(), 500, "platform canon is 500 bps");
        assertEq(factoring.factoringFeeBps(), FACTORING_FEE_BPS);

        (SealFactoring.AdvanceState state,,,,,, bytes32 recordedSigil) = _advance(taskId);
        assertEq(uint256(state), uint256(SealFactoring.AdvanceState.Settled));
        assertEq(recordedSigil, sigil);
    }

    function _advance(bytes32 taskId)
        internal
        view
        returns (
            SealFactoring.AdvanceState state,
            address worker_,
            address buyer_,
            uint256 gross,
            uint256 credit,
            uint256 spent,
            bytes32 sigil
        )
    {
        return factoring.advances(taskId);
    }

    // --- negatives: collateral ----------------------------------------------

    /// @notice THE core negative: a validly signed, registered SEAL whose
    ///         escrow was never funded gets NO advance. A signature alone
    ///         is not collateral.
    function testUnlockedSeal_GetsNoAdvance() public {
        bytes32 taskId = keccak256("task-unlocked");
        _register(taskId); // signed + registered, but lockEscrow never called
        assertFalse(vault.isLocked(taskId));
        assertEq(token.balanceOf(address(vault)), 0, "vault holds nothing for an unlocked seal");

        vm.expectRevert(SealFactoring.EscrowNotLocked.selector);
        factoring.issueAdvance(taskId, 100_000);
    }

    /// @notice A lock attempt that cannot pull funds (no approval) leaves
    ///         the seal unlocked — and still no advance is possible.
    function testUnfundedLock_Reverts_AndStillNoAdvance() public {
        // Fresh buyer with a seal but who never approved the vault.
        uint256 poorPk = 0x999;
        address poorBuyer = vm.addr(poorPk);
        SealAuthorization memory seal = SealAuthorization({
            taskId: keccak256("task-unfunded"),
            buyer: poorBuyer,
            provider: worker,
            amount: GROSS,
            expectedSchemaHash: schemaHash,
            deadline: block.timestamp + 7 days,
            nonce: 0
        });
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(poorPk, vault.hashSeal(seal));
        vault.registerSeal(seal, v, r, s);

        vm.expectRevert(); // transferFrom fails: no balance, no approval
        vault.lockEscrow(seal.taskId);
        assertFalse(vault.isLocked(seal.taskId));

        vm.expectRevert(SealFactoring.EscrowNotLocked.selector);
        factoring.issueAdvance(seal.taskId, 100_000);
    }

    function testInvalidSealSignature_Reverts() public {
        SealAuthorization memory seal = _seal(keccak256("task-badsig"), 0);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(0xBAD, vault.hashSeal(seal)); // not the buyer
        vm.expectRevert(SealEscrowVault.InvalidSealSignature.selector);
        vault.registerSeal(seal, v, r, s);
    }

    function testSealNonceReplay_Reverts() public {
        bytes32 taskId = keccak256("task-nonce");
        SealAuthorization memory seal = _register(taskId);
        // Same taskId again -> already registered.
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(buyerPk, vault.hashSeal(seal));
        vm.expectRevert(SealEscrowVault.SealAlreadyRegistered.selector);
        vault.registerSeal(seal, v, r, s);
        // Different task, stale nonce 0 (buyer nonce is now 1) -> bad nonce.
        SealAuthorization memory seal2 = _seal(keccak256("task-nonce-2"), 0);
        (v, r, s) = vm.sign(buyerPk, vault.hashSeal(seal2));
        vm.expectRevert(abi.encodeWithSelector(SealEscrowVault.InvalidNonce.selector, 1, 0));
        vault.registerSeal(seal2, v, r, s);
    }

    // --- negatives: advance & spend ------------------------------------------

    function testDoubleAdvance_Reverts() public {
        bytes32 taskId = keccak256("task-double");
        _registerAndLock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        vm.expectRevert(SealFactoring.AdvanceAlreadyExists.selector);
        factoring.issueAdvance(taskId, 100_000);
    }

    function testAdvanceExceedsMax_Reverts() public {
        bytes32 taskId = keccak256("task-toobig");
        _registerAndLock(taskId);
        vm.expectRevert(abi.encodeWithSelector(SealFactoring.AdvanceExceedsMax.selector, 760_001, 760_000));
        factoring.issueAdvance(taskId, 760_001);
    }

    function testNonAllowlistedPayee_Reverts() public {
        bytes32 taskId = keccak256("task-rogue");
        _registerAndLock(taskId);
        factoring.issueAdvance(taskId, 200_000);
        vm.prank(worker);
        vm.expectRevert(abi.encodeWithSelector(SealFactoring.PayeeNotAllowlisted.selector, roguePayee));
        factoring.spendAdvance(taskId, roguePayee, 50_000);
        assertEq(token.balanceOf(roguePayee), 0);
    }

    function testSpendExceedsCredit_Reverts() public {
        bytes32 taskId = keccak256("task-overspend");
        _registerAndLock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        vm.prank(worker);
        vm.expectRevert(abi.encodeWithSelector(SealFactoring.SpendExceedsCredit.selector, 100_001, 100_000));
        factoring.spendAdvance(taskId, payee1, 100_001);
    }

    function testSpendByNonWorker_Reverts() public {
        bytes32 taskId = keccak256("task-nonworker");
        _registerAndLock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        vm.prank(stranger);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.spendAdvance(taskId, payee1, 10_000);
    }

    // --- negatives: ordering ---------------------------------------------------

    /// @notice Settle-before-advance: the factor refuses to settle a task it
    ///         never factored (unfactored tasks use the plain Hub path).
    function testSettleBeforeAdvance_Reverts() public {
        bytes32 taskId = keccak256("task-early-settle");
        _registerAndLock(taskId);
        vm.prank(settler);
        vm.expectRevert(SealFactoring.NoActiveAdvance.selector);
        factoring.settle(taskId, keccak256("sigil"));
    }

    function testAdvanceAfterSettle_Reverts() public {
        bytes32 taskId = keccak256("task-late-advance");
        _registerAndLock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        _attestAndSettle(taskId, keccak256("sigil"));
        assertFalse(vault.isLocked(taskId), "escrow consumed by settlement");
        vm.expectRevert(SealFactoring.AdvanceAlreadyExists.selector);
        factoring.issueAdvance(taskId, 100_000);
    }

    function testSettleZeroSigil_AndNonSettler_Revert() public {
        bytes32 taskId = keccak256("task-settle-guards");
        _registerAndLock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        vm.prank(settler);
        vm.expectRevert(SealFactoring.SigilCommitmentZero.selector);
        factoring.settle(taskId, bytes32(0));
        vm.prank(stranger);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.settle(taskId, keccak256("sigil"));
    }

    // --- failure / clawback -----------------------------------------------------

    /// @notice Failure path, exact math: spent 300_000, fee on spent = 600,
    ///         owed 300_600; worker bond 100_000 seized; bad debt 200_600
    ///         booked against the worker; buyer refunded the full locked
    ///         gross from the vault; unspent credit (200_000) cancelled.
    function testDefaultClawback_ExactMath() public {
        bytes32 taskId = keccak256("task-failure");
        _registerAndLock(taskId);
        uint256 buyerAfterLock = token.balanceOf(buyer);

        vm.prank(worker);
        factoring.postBond(100_000);
        factoring.issueAdvance(taskId, 500_000);
        vm.prank(worker);
        factoring.spendAdvance(taskId, payee1, 300_000);
        assertEq(token.balanceOf(payee1), 300_000, "spent funds stay with the upstream payee");

        // FIX-PASS FLOW MIGRATION (assertions below unchanged): the settler
        // can no longer fail an undisputed advance unilaterally — the buyer
        // opens the failure dispute and the settler adjudicates it.
        vm.prank(buyer);
        factoring.markFailed(taskId); // opens dispute only
        vm.prank(settler);
        factoring.markFailed(taskId); // settler adjudicates the open dispute

        assertEq(token.balanceOf(buyer), buyerAfterLock + GROSS, "buyer made whole from escrow");
        assertEq(token.balanceOf(address(vault)), 0);
        assertEq(factoring.bondOf(worker), 0, "bond fully seized");
        assertEq(factoring.clawbackDebtOf(worker), 200_600, "residual loss booked exactly");
        assertEq(factoring.totalOutstandingCredit(), 0, "unspent credit cancelled");
        // Factor balance: 2_000_000 liquidity + 100_000 bond - 300_000 spent.
        assertEq(token.balanceOf(address(factoring)), LIQUIDITY + 100_000 - 300_000);
        (SealFactoring.AdvanceState state,,,,,,) = _advance(taskId);
        assertEq(uint256(state), uint256(SealFactoring.AdvanceState.Defaulted));
    }

    function testDefaultFullyCoveredByBond_NoBadDebt() public {
        bytes32 taskId = keccak256("task-failure-covered");
        _registerAndLock(taskId);
        vm.prank(worker);
        factoring.postBond(400_000);
        factoring.issueAdvance(taskId, 200_000);
        vm.prank(worker);
        factoring.spendAdvance(taskId, payee1, 100_000); // owed = 100_000 + 200 fee = 100_200
        // FIX-PASS FLOW MIGRATION (assertions below unchanged): a buyer
        // markFailed now OPENS a dispute instead of executing failure
        // unilaterally; the failure finalizes once the dispute window
        // lapses uncontested (the settler could have settled to contest).
        vm.prank(buyer);
        factoring.markFailed(taskId); // opens the failure dispute
        vm.warp(block.timestamp + factoring.DISPUTE_WINDOW() + 1);
        factoring.finalizeFailure(taskId); // permissionless after the window
        assertEq(factoring.bondOf(worker), 400_000 - 100_200, "bond seized exactly the amount owed");
        assertEq(factoring.clawbackDebtOf(worker), 0, "no residual loss");
    }

    function testMarkFailedByStranger_Reverts() public {
        bytes32 taskId = keccak256("task-fail-stranger");
        _registerAndLock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        vm.prank(stranger);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.markFailed(taskId);
    }

    function testExpireBeforeDeadline_Reverts_ThenPermissionlessAfter() public {
        bytes32 taskId = keccak256("task-expire");
        SealAuthorization memory seal = _registerAndLock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        vm.prank(worker);
        factoring.spendAdvance(taskId, payee1, 40_000);

        vm.prank(stranger);
        vm.expectRevert(SealFactoring.TaskNotYetExpired.selector);
        factoring.expire(taskId);

        vm.warp(seal.deadline + 1);
        uint256 buyerBefore = token.balanceOf(buyer);
        vm.prank(stranger); // anyone may trigger after the deadline
        factoring.expire(taskId);
        assertEq(token.balanceOf(buyer), buyerBefore + GROSS, "buyer refunded after expiry");
        // owed = 40_000 + 80 fee = 40_080, no bond posted -> all bad debt.
        assertEq(factoring.clawbackDebtOf(worker), 40_080);
    }

    // --- pricing guardrails -------------------------------------------------------

    function testFactoringFeeCap_Enforced() public {
        vm.prank(admin);
        vm.expectRevert(abi.encodeWithSelector(SealFactoring.FeeTooHigh.selector, 501));
        factoring.setFactoringFeeBps(501);
        vm.prank(admin);
        factoring.setFactoringFeeBps(20);
        assertEq(factoring.factoringFeeBps(), 20);
        // Non-admin cannot touch pricing.
        vm.prank(stranger);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.setFactoringFeeBps(20);
    }
}
