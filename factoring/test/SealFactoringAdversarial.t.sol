// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {
    SealAuthorization,
    SealEscrowVault,
    SealFactoring
} from "../contracts/SealFactoring.sol";

contract AdvMockToken is ERC20 {
    constructor() ERC20("Mock USDC", "mUSDC") {}
    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

/// @notice Fee-on-transfer token: burns 10% of every transfer. Used to show
///         the vault's `locked` flag is set WITHOUT verifying the balance
///         delta, so the escrow is undercollateralized the moment it locks.
contract FeeOnTransferToken is ERC20 {
    constructor() ERC20("FeeToken", "FOT") {}
    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
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

/// @notice Token whose `transfer` tries to reenter the factoring contract
///         mid-transfer (the ERC-777/hook-style reentrancy probe, adapted to
///         a token hook since plain ERC20 never calls the payee).
contract ReentrantToken is ERC20 {
    SealFactoring public target;
    bytes32 public atkTaskId;
    address public atkPayee;
    bytes32 public atkSigil;
    uint8 public mode; // 1 = reenter spendAdvance, 2 = reenter settle
    bool public armed;
    bool public reentrySucceeded;

    constructor() ERC20("Reentrant", "REENT") {}
    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
    function arm(SealFactoring _target, bytes32 _taskId, address _payee, uint8 _mode, bytes32 _sigil) external {
        target = _target;
        atkTaskId = _taskId;
        atkPayee = _payee;
        mode = _mode;
        atkSigil = _sigil;
        armed = true;
        reentrySucceeded = false;
    }
    function transfer(address to, uint256 amount) public override returns (bool) {
        bool ok = super.transfer(to, amount);
        if (armed) {
            armed = false;
            if (mode == 1) {
                try target.spendAdvance(atkTaskId, atkPayee, 1) {
                    reentrySucceeded = true;
                } catch {}
            } else if (mode == 2) {
                try target.settle(atkTaskId, atkSigil) {
                    reentrySucceeded = true;
                } catch {}
            }
        }
        return ok;
    }
}

/**
 * @notice ADVERSARIAL REVIEW — Lane 4 SealFactoring. Written by the
 *         adversarial reviewer, not the lane builder. Tests named
 *         `..._Reverts` / `..._Blocked` assert a defense held. All other
 *         tests assert that an attack SUCCEEDED (attacker profit, stolen
 *         remainder, drained escrow, or a state the design says should
 *         not happen) — they pass precisely because the flaw is real.
 */
contract SealFactoringAdversarialTest is Test {
    AdvMockToken internal token;
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
    address internal attacker = address(0x666);

    uint256 internal constant GROSS = 1_000_000;
    uint256 internal constant LIQUIDITY = 2_000_000;
    uint256 internal constant FACTORING_FEE_BPS = 20;
    uint256 internal constant ADVANCE_BPS = 8_000;
    uint256 internal constant MAX_ADVANCE = 760_000; // (1M - 50k) * 80%

    bytes32 internal schemaHash = keccak256("task-spec-v1");

    function setUp() public {
        buyer = vm.addr(buyerPk);
        token = new AdvMockToken();
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

    function _sealCustom(
        bytes32 taskId,
        address _buyer,
        address _provider,
        uint256 amount,
        uint256 deadline,
        uint256 nonce
    ) internal view returns (SealAuthorization memory) {
        return SealAuthorization({
            taskId: taskId,
            buyer: _buyer,
            provider: _provider,
            amount: amount,
            expectedSchemaHash: schemaHash,
            deadline: deadline,
            nonce: nonce
        });
    }

    function _seal(bytes32 taskId, uint256 nonce) internal view returns (SealAuthorization memory) {
        return _sealCustom(taskId, buyer, worker, GROSS, block.timestamp + 7 days, nonce);
    }

    function _registerWith(SealEscrowVault v, SealAuthorization memory seal, uint256 pk) internal {
        (uint8 vv, bytes32 r, bytes32 s) = vm.sign(pk, v.hashSeal(seal));
        v.registerSeal(seal, vv, r, s);
    }

    function _register(bytes32 taskId) internal returns (SealAuthorization memory seal) {
        seal = _seal(taskId, vault.nonces(buyer));
        _registerWith(vault, seal, buyerPk);
    }

    function _registerAndLock(bytes32 taskId) internal returns (SealAuthorization memory seal) {
        seal = _register(taskId);
        vault.lockEscrow(taskId);
    }

    /// @notice FIX-PASS helpers: settlement now requires the verifier's
    ///         on-chain attestation (verifier defaults to `admin`, a role
    ///         distinct from `settler`) before the settler may execute.
    function _attest(bytes32 taskId, bytes32 sigil) internal {
        vm.prank(admin);
        factoring.attestVerification(taskId, sigil);
    }

    function _attestAndSettle(bytes32 taskId, bytes32 sigil) internal {
        _attest(taskId, sigil);
        vm.prank(settler);
        factoring.settle(taskId, sigil);
    }

    function _attestOn(SealFactoring f, bytes32 taskId, bytes32 sigil) internal {
        vm.prank(admin);
        f.attestVerification(taskId, sigil);
    }

    function _state(bytes32 taskId) internal view returns (SealFactoring.AdvanceState) {
        (SealFactoring.AdvanceState st,,,,,,) = factoring.advances(taskId);
        return st;
    }

    // =====================================================================
    // COLLATERAL INTEGRITY
    // =====================================================================

    /// @notice FLIPPED BY FIX PASS (was CRITICAL / SUCCEEDED): the vault
    ///         admin's repoint is now only a PROPOSAL — timelocked, and its
    ///         execution reverts while any escrow is locked. The attacker
    ///         module never becomes the settlement module mid-flight, its
    ///         drain call reverts, and the legitimate settlement path works.
    function testAdv_VaultAdminRepoint_DrainsAllLockedEscrow() public {
        bytes32 taskId = keccak256("adv-repoint");
        _registerAndLock(taskId);
        factoring.issueAdvance(taskId, 200_000);
        vm.prank(worker);
        factoring.spendAdvance(taskId, payee1, 50_000);

        vm.prank(admin);
        vault.setSettlementModule(attacker); // now only PROPOSES the change
        assertEq(vault.settlementModule(), address(factoring), "module unchanged until execution");
        assertEq(vault.pendingSettlementModule(), attacker, "change is merely pending");

        vm.prank(attacker);
        vm.expectRevert(SealEscrowVault.Unauthorized.selector);
        vault.releaseForSettlement(taskId, attacker);
        assertEq(token.balanceOf(attacker), 0, "attacker drained nothing");
        assertEq(token.balanceOf(address(vault)), GROSS, "escrow intact");

        // Even after the timelock delay, execution is blocked while locked.
        vm.warp(block.timestamp + vault.MODULE_CHANGE_DELAY() + 1);
        vm.prank(admin);
        vm.expectRevert(
            abi.encodeWithSelector(SealEscrowVault.ModuleChangeBlockedByActiveEscrow.selector, 1)
        );
        vault.executeSettlementModuleChange();

        // The legitimate settlement path is alive and settles exactly.
        _attestAndSettle(taskId, keccak256("sigil"));
        assertEq(uint256(_state(taskId)), uint256(SealFactoring.AdvanceState.Settled));
        assertEq(token.balanceOf(address(vault)), 0, "vault drained by settlement, not by attacker");
        assertEq(token.balanceOf(attacker), 0, "attacker still holds nothing");

        // With no escrow locked, the timelocked change can now execute.
        vm.prank(admin);
        vault.executeSettlementModuleChange();
        assertEq(vault.settlementModule(), attacker, "repoint applies only once nothing is at risk");
    }

    /// @notice FLIPPED BY FIX PASS (was SUCCEEDED): a 1-wei dust advance
    ///         now reverts `AdvanceBelowMinimum` (MIN_ADVANCE = 1_000), so
    ///         the taskId stays free for the real advance.
    function testAdv_DustAdvance_GriefsTaskPermanently() public {
        bytes32 taskId = keccak256("adv-dust");
        _registerAndLock(taskId);
        vm.prank(stranger);
        vm.expectRevert(
            abi.encodeWithSelector(SealFactoring.AdvanceBelowMinimum.selector, 1, factoring.MIN_ADVANCE())
        );
        factoring.issueAdvance(taskId, 1);
        assertEq(uint256(_state(taskId)), uint256(SealFactoring.AdvanceState.None), "no dust advance recorded");
        factoring.issueAdvance(taskId, 500_000);
        (,,, uint256 creditIssued,,) = _adv(taskId);
        assertEq(creditIssued, 500_000, "the real advance issues normally");
    }

    function _adv(bytes32 taskId)
        internal
        view
        returns (address w, address b, uint256 gross, uint256 credit, uint256 spent, bytes32 sigil)
    {
        (, w, b, gross, credit, spent, sigil) = factoring.advances(taskId);
    }

    /// @notice FLIPPED BY FIX PASS (was CRITICAL / SUCCEEDED): buyer ==
    ///         provider seals are now rejected at issuance with
    ///         `SelfDealingBlocked`. The recycle never starts: no advance,
    ///         no spend, no factor loss, no debt. (The second line of
    ///         defense — clawback debt gating future advances — is covered
    ///         by testFix_DebtGate_BlocksNewAdvanceAfterDefault.)
    function testAdv_SelfDealing_RecycledCollateralDrainsFactor_Twice() public {
        uint256 selfPk = 0xCAFE;
        address self = vm.addr(selfPk);
        token.mint(self, 3_000_000);
        vm.startPrank(self);
        token.approve(address(vault), type(uint256).max);
        token.approve(address(factoring), type(uint256).max);
        vm.stopPrank();

        for (uint256 i = 0; i < 2; i++) {
            bytes32 taskId = keccak256(abi.encodePacked("adv-selfdeal-", i));
            SealAuthorization memory seal = _sealCustom(
                taskId, self, self, GROSS, block.timestamp + 100, vault.nonces(self)
            );
            _registerWith(vault, seal, selfPk);
            vault.lockEscrow(taskId);
            vm.expectRevert(SealFactoring.SelfDealingBlocked.selector);
            factoring.issueAdvance(taskId, MAX_ADVANCE);
            assertEq(uint256(_state(taskId)), uint256(SealFactoring.AdvanceState.None), "no advance issued");
        }
        assertEq(token.balanceOf(payee1), 0, "accomplice payee received nothing");
        assertEq(token.balanceOf(address(factoring)), LIQUIDITY, "factor liquidity untouched");
        assertEq(factoring.clawbackDebtOf(self), 0, "no debt cycle ever started");
    }

    /// @notice SUCCEEDED (documented trust assumption 5 — bounded as
    ///         documented, but the bound is the entire max advance): zero
    ///         bond, max spend, default. Loss = maxAdvance principal; the
    ///         booked debt (principal + fee) has no collection mechanism.
    function testAdv_ZeroBond_MaxSpendDefault_BadDebtBoundedButUncollectible() public {
        bytes32 taskId = keccak256("adv-zerobond");
        SealAuthorization memory seal = _registerAndLock(taskId);
        factoring.issueAdvance(taskId, MAX_ADVANCE);
        vm.prank(worker);
        factoring.spendAdvance(taskId, payee1, MAX_ADVANCE);
        vm.warp(seal.deadline + 1);
        vm.prank(stranger);
        factoring.expire(taskId);
        assertEq(factoring.clawbackDebtOf(worker), MAX_ADVANCE + factoring.factoringFeeOn(MAX_ADVANCE));
        assertEq(token.balanceOf(address(factoring)), LIQUIDITY - MAX_ADVANCE, "factor ate the full principal");
        assertEq(token.balanceOf(buyer), 5_000_000, "buyer fully refunded");
    }

    /// @notice BLOCKED: pooled vault balance does not collateralize a
    ///         different seal, and the advance cap is per-seal, not pooled.
    function testAdv_CrossSealConfusion_Blocked() public {
        bytes32 taskA = keccak256("adv-cross-a");
        _registerAndLock(taskA); // vault now holds 1M for A

        bytes32 taskB = keccak256("adv-cross-b");
        _register(taskB); // registered, never locked
        vm.expectRevert(SealFactoring.EscrowNotLocked.selector);
        factoring.issueAdvance(taskB, 100_000);

        bytes32 taskC = keccak256("adv-cross-c");
        SealAuthorization memory sealC =
            _sealCustom(taskC, buyer, worker, 100_000, block.timestamp + 7 days, vault.nonces(buyer));
        _registerWith(vault, sealC, buyerPk);
        vault.lockEscrow(taskC);
        // C's own cap is (100k - 5k) * 80% = 76k, despite 1.1M pooled in vault.
        vm.expectRevert(abi.encodeWithSelector(SealFactoring.AdvanceExceedsMax.selector, 100_000, 76_000));
        factoring.issueAdvance(taskC, 100_000);
    }

    /// @notice BLOCKED: no re-lock, no unlock/withdraw path exists.
    function testAdv_RelockAndUnlock_Blocked() public {
        bytes32 taskId = keccak256("adv-relock");
        _registerAndLock(taskId);
        vm.expectRevert(SealEscrowVault.SealAlreadyLocked.selector);
        vault.lockEscrow(taskId);
        // Stranger cannot pull funds out through either vault exit.
        vm.prank(stranger);
        vm.expectRevert(SealEscrowVault.Unauthorized.selector);
        vault.releaseForSettlement(taskId, stranger);
        vm.prank(stranger);
        vm.expectRevert(SealEscrowVault.Unauthorized.selector);
        vault.refundOnFailure(taskId);
        // Even after settlement the seal cannot be re-locked for a 2nd round.
        factoring.issueAdvance(taskId, 100_000);
        _attestAndSettle(taskId, keccak256("sigil"));
        vm.expectRevert(SealEscrowVault.SealAlreadyLocked.selector);
        vault.lockEscrow(taskId);
    }

    // =====================================================================
    // SIGNATURE / NONCE
    // =====================================================================

    /// @notice BLOCKED: domain separation — a signature for vault #1 does
    ///         not verify on vault #2 (verifyingContract is in the domain).
    function testAdv_CrossVaultReplay_Reverts() public {
        SealEscrowVault vault2 = new SealEscrowVault(address(token), admin);
        SealAuthorization memory seal = _seal(keccak256("adv-xvault"), 0);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(buyerPk, vault.hashSeal(seal));
        vm.expectRevert(SealEscrowVault.InvalidSealSignature.selector);
        vault2.registerSeal(seal, v, r, s);
    }

    /// @notice BLOCKED: domain separation — a signature made on this chain
    ///         does not verify after a chainId change.
    function testAdv_CrossChainReplay_Reverts() public {
        SealAuthorization memory seal = _seal(keccak256("adv-xchain"), 0);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(buyerPk, vault.hashSeal(seal));
        vm.chainId(999_999);
        vm.expectRevert(SealEscrowVault.InvalidSealSignature.selector);
        vault.registerSeal(seal, v, r, s);
    }

    /// @notice BLOCKED: ECDSA malleability — the (v^1, r, n-s) twin of a
    ///         valid signature is rejected by the HALF_N guard.
    function testAdv_SignatureMalleability_Reverts() public {
        SealAuthorization memory seal = _seal(keccak256("adv-malleable"), 0);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(buyerPk, vault.hashSeal(seal));
        uint256 n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141;
        bytes32 s2 = bytes32(n - uint256(s));
        uint8 v2 = v == 27 ? 28 : 27;
        vm.expectRevert(SealEscrowVault.InvalidSealSignature.selector);
        vault.registerSeal(seal, v2, r, s2);
    }

    /// @notice BLOCKED: the provider (worker) cannot sign a seal naming
    ///         someone else as buyer.
    function testAdv_WrongSigner_Reverts() public {
        SealAuthorization memory seal = _seal(keccak256("adv-wrongsigner"), 0);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(0xBAD, vault.hashSeal(seal));
        vm.expectRevert(SealEscrowVault.InvalidSealSignature.selector);
        vault.registerSeal(seal, v, r, s);
    }

    /// @notice BLOCKED: an already-expired seal cannot be registered, and a
    ///         seal that expires before locking can never become collateral.
    function testAdv_ExpiredSeal_RegisterAndLock_Revert() public {
        vm.warp(1_000_000);
        SealAuthorization memory dead = _sealCustom(
            keccak256("adv-dead"), buyer, worker, GROSS, 999_999, vault.nonces(buyer)
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(buyerPk, vault.hashSeal(dead));
        vm.expectRevert(SealEscrowVault.SealExpired.selector);
        vault.registerSeal(dead, v, r, s);

        bytes32 taskId = keccak256("adv-expire-before-lock");
        SealAuthorization memory seal =
            _sealCustom(taskId, buyer, worker, GROSS, block.timestamp + 100, vault.nonces(buyer));
        _registerWith(vault, seal, buyerPk);
        vm.warp(block.timestamp + 101);
        vm.expectRevert(SealEscrowVault.SealExpired.selector);
        vault.lockEscrow(taskId);
        vm.expectRevert(SealFactoring.EscrowNotLocked.selector);
        factoring.issueAdvance(taskId, 100_000);
    }

    /// @notice BLOCKED: the buyer's nonce stays consumed after settlement —
    ///         the same nonce cannot mint a fresh seal afterwards.
    function testAdv_NonceReuseAfterSettle_Reverts() public {
        bytes32 taskId = keccak256("adv-nonce-settle");
        _registerAndLock(taskId); // consumes nonce 0
        factoring.issueAdvance(taskId, 10_000);
        _attestAndSettle(taskId, keccak256("sigil"));
        SealAuthorization memory seal2 = _seal(keccak256("adv-nonce-reuse"), 0);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(buyerPk, vault.hashSeal(seal2));
        vm.expectRevert(abi.encodeWithSelector(SealEscrowVault.InvalidNonce.selector, 1, 0));
        vault.registerSeal(seal2, v, r, s);
    }

    // =====================================================================
    // SPEND SCOPING
    // =====================================================================

    /// @notice FLIPPED BY FIX PASS (was HIGH / SUCCEEDED): the allowlist is
    ///         snapshotted at issuance, so admin allowlisting ITSELF
    ///         mid-flight has NO effect on the in-flight advance — the
    ///         colluding spend reverts and nothing is extracted.
    function testAdv_AdminWorkerCollusion_AllowlistedSelfDrain_OnDefault() public {
        bytes32 taskId = keccak256("adv-collusion");
        _registerAndLock(taskId);
        factoring.issueAdvance(taskId, 500_000);
        vm.prank(admin);
        factoring.setPayeeAllowlist(admin, true); // admin allowlists itself, mid-flight
        assertTrue(factoring.isAllowlistedPayee(admin), "live allowlist does include admin now");
        vm.prank(worker);
        vm.expectRevert(abi.encodeWithSelector(SealFactoring.PayeeNotAllowlisted.selector, admin));
        factoring.spendAdvance(taskId, admin, 500_000);
        assertEq(token.balanceOf(admin), 0, "admin extracted nothing");
        assertEq(token.balanceOf(address(factoring)), LIQUIDITY, "factor lost nothing");
        assertEq(factoring.clawbackDebtOf(worker), 0, "no default, no debt");
        // The issuance-time snapshot still honors the ORIGINAL payees.
        vm.prank(worker);
        factoring.spendAdvance(taskId, payee1, 500_000);
        assertEq(token.balanceOf(payee1), 500_000, "snapshot-allowlisted payee still spendable");
    }

    /// @notice FLIPPED BY FIX PASS (was SUCCEEDED): allowlist mutations no
    ///         longer affect an in-flight advance in EITHER direction —
    ///         removal does not brick a payee that was allowlisted at
    ///         issuance, and addition does not legitimize a rogue payee.
    function testAdv_AllowlistMutationMidFlight_AddAndRemove() public {
        bytes32 taskId = keccak256("adv-allowlist-mutate");
        _registerAndLock(taskId);
        factoring.issueAdvance(taskId, 200_000);
        vm.prank(admin);
        factoring.setPayeeAllowlist(payee1, false); // removed AFTER issuance
        vm.prank(worker);
        factoring.spendAdvance(taskId, payee1, 10_000); // snapshot still allows it
        assertEq(token.balanceOf(payee1), 10_000, "issuance snapshot governs, not the live removal");
        vm.prank(admin);
        factoring.setPayeeAllowlist(roguePayee, true); // added AFTER issuance
        vm.prank(worker);
        vm.expectRevert(abi.encodeWithSelector(SealFactoring.PayeeNotAllowlisted.selector, roguePayee));
        factoring.spendAdvance(taskId, roguePayee, 10_000);
        assertEq(token.balanceOf(roguePayee), 0, "rogue payee NOT legitimized mid-flight");
    }

    /// @notice BLOCKED: no spending after settlement or after default.
    ///         (Setup migrated for the fix pass: settle needs a verifier
    ///         attestation; failure goes through buyer dispute + settler
    ///         adjudication. Assertions unchanged.)
    function testAdv_SpendAfterSettleAndAfterDefault_Reverts() public {
        bytes32 t1 = keccak256("adv-spend-after-settle");
        _registerAndLock(t1);
        factoring.issueAdvance(t1, 100_000);
        _attestAndSettle(t1, keccak256("sigil"));
        vm.prank(worker);
        vm.expectRevert(SealFactoring.AdvanceNotActive.selector);
        factoring.spendAdvance(t1, payee1, 1_000);

        bytes32 t2 = keccak256("adv-spend-after-default");
        _registerAndLock(t2);
        factoring.issueAdvance(t2, 100_000);
        vm.prank(buyer);
        factoring.markFailed(t2); // opens dispute
        vm.prank(settler);
        factoring.markFailed(t2); // adjudicates -> Defaulted
        vm.prank(worker);
        vm.expectRevert(SealFactoring.AdvanceNotActive.selector);
        factoring.spendAdvance(t2, payee1, 1_000);
    }

    /// @notice FLIPPED BY FIX PASS (was SUCCEEDED): spendAdvance now checks
    ///         the seal deadline under the single deadline rule — after the
    ///         deadline the spend reverts `SealExpired`.
    function testAdv_SpendAfterDeadline_StillSucceeds() public {
        bytes32 taskId = keccak256("adv-spend-late");
        SealAuthorization memory seal =
            _sealCustom(taskId, buyer, worker, GROSS, block.timestamp + 100, vault.nonces(buyer));
        _registerWith(vault, seal, buyerPk);
        vault.lockEscrow(taskId);
        factoring.issueAdvance(taskId, 100_000);
        vm.warp(block.timestamp + 101); // past deadline, expire() not yet called
        vm.prank(worker);
        vm.expectRevert(SealFactoring.SealExpired.selector);
        factoring.spendAdvance(taskId, payee1, 100_000);
        assertEq(token.balanceOf(payee1), 0, "nothing spent after the deadline");
    }

    // =====================================================================
    // WATERFALL MATH / STATE RACES
    // =====================================================================

    /// @notice FLIPPED BY FIX PASS (was HIGH / SUCCEEDED): the factoring
    ///         fee is snapshotted at issuance, so the admin's mid-flight
    ///         hike to the 500 bps cap has NO effect on this advance — the
    ///         worker's remainder is computed at the issuance rate (20 bps).
    function testAdv_FeeHikeMidFlight_StealsWorkerRemainder() public {
        bytes32 taskId = keccak256("adv-feehike");
        _registerAndLock(taskId);
        factoring.issueAdvance(taskId, 500_000);
        assertEq(factoring.advanceFeeBpsSnapshot(taskId), 20, "fee snapshotted at issuance");
        vm.prank(worker);
        factoring.spendAdvance(taskId, payee1, 400_000);
        vm.prank(admin);
        factoring.setFactoringFeeBps(500); // at the hard cap, mid-flight
        uint256 workerBefore = token.balanceOf(worker);
        _attestAndSettle(taskId, keccak256("sigil"));
        // Issuance terms preserved: remainder = 1M - 50k - 400k - 800 = 549_200.
        assertEq(token.balanceOf(worker), workerBefore + 549_200, "worker remainder NOT cut by mid-flight fee hike");
    }

    /// @notice BLOCKED: settle is single-shot.
    function testAdv_DoubleSettle_Reverts() public {
        bytes32 taskId = keccak256("adv-double-settle");
        _registerAndLock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        _attestAndSettle(taskId, keccak256("sigil"));
        vm.prank(settler);
        vm.expectRevert(SealFactoring.AdvanceNotActive.selector);
        factoring.settle(taskId, keccak256("sigil"));
    }

    /// @notice BLOCKED: settle-then-expire — the failure path cannot claw
    ///         back a settled advance.
    function testAdv_SettleThenExpire_Reverts() public {
        bytes32 taskId = keccak256("adv-settle-expire");
        SealAuthorization memory seal = _registerAndLock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        _attestAndSettle(taskId, keccak256("sigil"));
        vm.warp(seal.deadline + 1);
        vm.expectRevert(SealFactoring.AdvanceNotActive.selector);
        factoring.expire(taskId);
    }

    /// @notice FLIPPED BY FIX PASS (was SUCCEEDED): the race no longer
    ///         exists. Under the single deadline rule there is NO window in
    ///         which both settle and expire are valid: before the deadline
    ///         expire reverts; after it, settle deterministically reverts
    ///         `SealExpired` (checked before state, so the outcome does not
    ///         depend on which transaction is mined first) and expire is
    ///         the sole — defined, not raced — post-deadline path.
    function testAdv_ExpireFrontrunsSettleAfterDeadline() public {
        bytes32 taskId = keccak256("adv-race");
        SealAuthorization memory seal =
            _sealCustom(taskId, buyer, worker, GROSS, block.timestamp + 100, vault.nonces(buyer));
        _registerWith(vault, seal, buyerPk);
        vault.lockEscrow(taskId);
        factoring.issueAdvance(taskId, 100_000);
        vm.prank(worker);
        factoring.spendAdvance(taskId, payee1, 40_000);
        // Before the deadline, expire cannot destroy a settleable advance.
        vm.prank(stranger);
        vm.expectRevert(SealFactoring.TaskNotYetExpired.selector);
        factoring.expire(taskId);
        vm.warp(seal.deadline + 1);
        // After the deadline, settle is blocked by RULE, not by losing a race.
        _attest(taskId, keccak256("sigil"));
        vm.prank(settler);
        vm.expectRevert(SealFactoring.SealExpired.selector);
        factoring.settle(taskId, keccak256("sigil"));
        vm.prank(stranger);
        factoring.expire(taskId);
        assertEq(factoring.clawbackDebtOf(worker), 40_080, "post-deadline expiry books the same exact debt");
    }

    /// @notice FLIPPED BY FIX PASS (was SUCCEEDED): settle() now checks the
    ///         deadline — an expired seal cannot be settled even if nobody
    ///         called expire(); the worker is paid nothing and the advance
    ///         remains resolvable only through the failure/expiry path.
    function testAdv_SettleAfterDeadline_Succeeds() public {
        bytes32 taskId = keccak256("adv-settle-late");
        SealAuthorization memory seal =
            _sealCustom(taskId, buyer, worker, GROSS, block.timestamp + 100, vault.nonces(buyer));
        _registerWith(vault, seal, buyerPk);
        vault.lockEscrow(taskId);
        factoring.issueAdvance(taskId, 100_000);
        vm.warp(seal.deadline + 1);
        uint256 workerBefore = token.balanceOf(worker);
        _attest(taskId, keccak256("sigil")); // even a verified commitment...
        vm.prank(settler);
        vm.expectRevert(SealFactoring.SealExpired.selector); // ...cannot settle late
        factoring.settle(taskId, keccak256("sigil"));
        assertEq(token.balanceOf(worker), workerBefore, "expired seal NOT settled");
        assertEq(uint256(_state(taskId)), uint256(SealFactoring.AdvanceState.Active), "advance awaits expiry path");
    }

    /// @notice FLIPPED BY FIX PASS (was SUCCEEDED, low/dust): the platform
    ///         fee now uses CEIL division, so it never truncates to 0.
    ///         Scenario adjusted to a factorable size (the original gross-19
    ///         seal is below MIN_ADVANCE and can no longer be factored at
    ///         all): gross 10_001 -> floor fee would be 500, ceil fee is
    ///         exactly 501; the rate itself is unchanged (500 bps canon).
    function testAdv_TinySeal_PlatformFeeRoundsToZero() public {
        bytes32 taskId = keccak256("adv-tiny");
        SealAuthorization memory seal =
            _sealCustom(taskId, buyer, worker, 10_001, block.timestamp + 7 days, vault.nonces(buyer));
        _registerWith(vault, seal, buyerPk);
        vault.lockEscrow(taskId);
        factoring.issueAdvance(taskId, 1_000);
        uint256 workerBefore = token.balanceOf(worker);
        _attestAndSettle(taskId, keccak256("sigil"));
        assertEq(token.balanceOf(treasury), 501, "platform fee ceils to 501, never truncates toward 0");
        assertEq(token.balanceOf(worker), workerBefore + 9_500, "worker remainder = 10_001 - 501 exactly");
        assertEq(factoring.PLATFORM_FEE_BPS(), 500, "rate unchanged: 500 bps canon");
    }

    /// @notice BLOCKED / exact: zero-spend and full-spend settlement edges
    ///         compute exactly, with no underflow and no phantom recovery of
    ///         unspent credit.
    function testAdv_ZeroSpendAndFullSpendSettle_Exact() public {
        bytes32 t1 = keccak256("adv-settle-zeropspend");
        _registerAndLock(t1);
        factoring.issueAdvance(t1, 500_000);
        uint256 w0 = token.balanceOf(worker);
        _attestAndSettle(t1, keccak256("sigil"));
        assertEq(token.balanceOf(worker), w0 + 950_000, "zero spend: worker gets gross minus 5% only");
        assertEq(token.balanceOf(address(factoring)), LIQUIDITY, "zero spend: factor neither gains nor loses");

        bytes32 t2 = keccak256("adv-settle-fullspend");
        _registerAndLock(t2);
        factoring.issueAdvance(t2, 500_000);
        vm.prank(worker);
        factoring.spendAdvance(t2, payee1, 500_000);
        uint256 w1 = token.balanceOf(worker);
        _attestAndSettle(t2, keccak256("sigil"));
        // remainder = 1M - 50k - 500k - 1_000 (fee on 500k @20bps) = 449_000
        assertEq(token.balanceOf(worker), w1 + 449_000, "full spend: exact remainder");
    }

    // =====================================================================
    // ROLES
    // =====================================================================

    /// @notice FLIPPED BY FIX PASS (was CRITICAL / SUCCEEDED): the settler
    ///         ALONE can no longer settle unverified work — settle requires
    ///         the independent verifier's on-chain attestation of the exact
    ///         commitment (`VerificationRequired` otherwise). RESIDUAL
    ///         TRUST, stated plainly: a colluding verifier + settler pair
    ///         can still settle garbage; what changed is that it now takes
    ///         TWO independently custodied keys instead of one.
    function testAdv_SettlerSettlesUnverifiedWork_BuyerLosesEscrow() public {
        bytes32 taskId = keccak256("adv-settler-trust");
        _registerAndLock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        uint256 workerBefore = token.balanceOf(worker);
        uint256 buyerBefore = token.balanceOf(buyer);
        vm.prank(settler);
        vm.expectRevert(SealFactoring.VerificationRequired.selector);
        factoring.settle(taskId, keccak256("never-verified-garbage"));
        assertEq(token.balanceOf(worker), workerBefore, "worker NOT paid for unverified work");
        assertEq(token.balanceOf(buyer), buyerBefore, "buyer escrow untouched");
        assertEq(token.balanceOf(treasury), 0, "no platform fee taken on a blocked settle");
        assertEq(uint256(_state(taskId)), uint256(SealFactoring.AdvanceState.Active), "advance still active");
        // Even an attestation for a DIFFERENT commitment does not help.
        _attest(taskId, keccak256("some-other-output"));
        vm.prank(settler);
        vm.expectRevert(SealFactoring.VerificationRequired.selector);
        factoring.settle(taskId, keccak256("never-verified-garbage"));
    }

    /// @notice FLIPPED BY FIX PASS (was SUCCEEDED): the settler can no
    ///         longer unilaterally fail a healthy, undisputed, pre-deadline
    ///         advance (`FailureDisputeRequired`). The constrained path
    ///         still works: once the BUYER opens a dispute, the settler may
    ///         adjudicate it — with the exact same clawback math as before.
    function testAdv_SettlerMarkFailed_GriefsWorker() public {
        bytes32 taskId = keccak256("adv-settler-fail");
        _registerAndLock(taskId);
        vm.prank(worker);
        factoring.postBond(50_000);
        factoring.issueAdvance(taskId, 200_000);
        vm.prank(worker);
        factoring.spendAdvance(taskId, payee1, 100_000);
        vm.prank(settler);
        vm.expectRevert(SealFactoring.FailureDisputeRequired.selector);
        factoring.markFailed(taskId);
        assertEq(factoring.bondOf(worker), 50_000, "bond NOT seized on settler's word alone");
        assertEq(factoring.clawbackDebtOf(worker), 0, "no debt booked");
        assertEq(uint256(_state(taskId)), uint256(SealFactoring.AdvanceState.Active), "advance still active");
        // Constrained path: buyer disputes -> settler adjudicates.
        vm.prank(buyer);
        factoring.markFailed(taskId); // opens dispute
        vm.prank(settler);
        factoring.markFailed(taskId); // adjudicates the open dispute
        assertEq(factoring.bondOf(worker), 0, "bond seized only via the dispute path");
        assertEq(factoring.clawbackDebtOf(worker), 100_200 - 50_000, "residual debt booked exactly");
        assertEq(token.balanceOf(buyer), 5_000_000, "buyer refunded in full");
    }

    /// @notice FLIPPED BY FIX PASS (was SUCCEEDED): the buyer's markFailed
    ///         now only OPENS a dispute — no refund, no bond seizure, no
    ///         debt — and inside the dispute window the settler can CONTEST
    ///         by settling the (verified) work, which pays the worker the
    ///         full remainder and kills the buyer's failure path.
    function testAdv_BuyerUnilateralMarkFailed_AfterSpend() public {
        bytes32 taskId = keccak256("adv-buyer-fail");
        _registerAndLock(taskId);
        vm.prank(worker);
        factoring.postBond(50_000);
        factoring.issueAdvance(taskId, 500_000);
        vm.prank(worker);
        factoring.spendAdvance(taskId, payee1, 300_000);
        uint256 buyerBefore = token.balanceOf(buyer);
        vm.prank(buyer);
        factoring.markFailed(taskId); // opens dispute ONLY
        assertEq(token.balanceOf(buyer), buyerBefore, "buyer NOT refunded by a unilateral call");
        assertEq(factoring.bondOf(worker), 50_000, "worker bond NOT seized");
        assertEq(factoring.clawbackDebtOf(worker), 0, "worker NOT booked as defaulter");
        assertTrue(factoring.failureDisputed(taskId), "dispute is open");
        assertEq(uint256(_state(taskId)), uint256(SealFactoring.AdvanceState.Active), "advance still active");
        // Finalizing early is blocked while the window is open.
        vm.expectRevert(
            abi.encodeWithSelector(
                SealFactoring.DisputeWindowStillOpen.selector,
                factoring.failureDisputeOpenedAt(taskId) + factoring.DISPUTE_WINDOW()
            )
        );
        factoring.finalizeFailure(taskId);
        // The settler contests by settling the verified work in-window.
        uint256 workerBefore = token.balanceOf(worker);
        _attestAndSettle(taskId, keccak256("sigil"));
        // remainder = 1M - 50k platform - 300k spent - 600 fee = 649_400
        assertEq(token.balanceOf(worker), workerBefore + 649_400, "worker paid despite the buyer's dispute");
        assertFalse(factoring.failureDisputed(taskId), "settlement clears the dispute");
    }

    /// @notice BLOCKED: role inventory for non-privileged callers — admin
    ///         cannot settle or spend, stranger cannot touch admin setters
    ///         or the vault exits, worker cannot settle.
    function testAdv_RoleInventory_NonPrivilegedCalls_Revert() public {
        bytes32 taskId = keccak256("adv-roles");
        _registerAndLock(taskId);
        factoring.issueAdvance(taskId, 100_000);

        vm.prank(admin); // admin is not the settler
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.settle(taskId, keccak256("sigil"));
        vm.prank(admin); // admin is not the worker
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.spendAdvance(taskId, payee1, 1_000);
        vm.prank(worker);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.settle(taskId, keccak256("sigil"));

        vm.startPrank(stranger);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.setPayeeAllowlist(stranger, true);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.setFactoringFeeBps(1);
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.setSettler(stranger);
        vm.expectRevert(SealEscrowVault.Unauthorized.selector);
        vault.setSettlementModule(stranger);
        vm.stopPrank();
    }

    // =====================================================================
    // TOKEN BEHAVIOR / REENTRANCY
    // =====================================================================

    /// @notice FLIPPED BY FIX PASS (was SUCCEEDED vs the accounting):
    ///         lockEscrow now measures the balance delta and accounts the
    ///         receivable against the 900k ACTUALLY received from a 10%
    ///         fee-on-transfer token. Credit is capped against the received
    ///         amount, and BOTH exits (settle and failure-refund) complete
    ///         — the escrow can no longer wedge on a phantom 1M.
    function testAdv_FeeOnTransfer_LockShortfall_SettleAndRefundStuck() public {
        FeeOnTransferToken ft = new FeeOnTransferToken();
        SealEscrowVault v2 = new SealEscrowVault(address(ft), admin);
        SealFactoring f2 = new SealFactoring(address(v2), treasury, admin, settler, FACTORING_FEE_BPS, ADVANCE_BPS);
        vm.prank(admin);
        v2.setSettlementModule(address(f2));
        vm.prank(admin);
        f2.setPayeeAllowlist(payee1, true);
        ft.mint(buyer, 5_000_000);
        ft.mint(funder, 5_000_000);
        vm.prank(buyer);
        ft.approve(address(v2), type(uint256).max);
        vm.prank(funder);
        ft.approve(address(f2), type(uint256).max);
        vm.prank(funder);
        f2.fundLiquidity(LIQUIDITY);

        bytes32 taskId = keccak256("adv-fot");
        SealAuthorization memory seal =
            _sealCustom(taskId, buyer, worker, GROSS, block.timestamp + 7 days, 0);
        _registerWith(v2, seal, buyerPk);
        v2.lockEscrow(taskId);
        assertTrue(v2.isLocked(taskId), "vault claims the seal is locked");
        assertEq(ft.balanceOf(address(v2)), 900_000, "only 900k actually arrived");
        assertEq(v2.lockedAmountOf(taskId), 900_000, "collateral accounted at the RECEIVED amount");
        // Max advance is now sized to the received 900k, not the phantom 1M:
        // (900k - 45k platform) * 80% = 684k.
        assertEq(f2.maxAdvanceFor(900_000), 684_000, "cap follows received collateral");

        f2.issueAdvance(taskId, 500_000);
        _attestOn(f2, taskId, keccak256("sigil"));
        vm.prank(settler);
        f2.settle(taskId, keccak256("sigil")); // no longer reverts
        assertEq(ft.balanceOf(address(v2)), 0, "vault fully drained by settlement, nothing stuck");
        (SealFactoring.AdvanceState st,,,,,,) = f2.advances(taskId);
        assertEq(uint256(st), uint256(SealFactoring.AdvanceState.Settled), "advance resolved via settle");

        // Refund path is unstuck too: a second FoT seal fails cleanly.
        bytes32 taskId2 = keccak256("adv-fot-2");
        SealAuthorization memory seal2 =
            _sealCustom(taskId2, buyer, worker, GROSS, block.timestamp + 7 days, 1);
        _registerWith(v2, seal2, buyerPk);
        v2.lockEscrow(taskId2);
        assertEq(v2.lockedAmountOf(taskId2), 900_000, "second seal also accounted at received amount");
        f2.issueAdvance(taskId2, 100_000);
        vm.prank(buyer);
        f2.markFailed(taskId2); // opens dispute
        vm.prank(settler);
        f2.markFailed(taskId2); // adjudicates -> refund of the received 900k
        assertEq(ft.balanceOf(address(v2)), 0, "refund moved exactly the received amount; vault empty");
        (SealFactoring.AdvanceState st2,,,,,,) = f2.advances(taskId2);
        assertEq(uint256(st2), uint256(SealFactoring.AdvanceState.Defaulted), "advance resolved via failure");
    }

    function _deployReentrant()
        internal
        returns (ReentrantToken rt, SealEscrowVault v, SealFactoring f)
    {
        rt = new ReentrantToken();
        v = new SealEscrowVault(address(rt), admin);
        f = new SealFactoring(address(v), treasury, admin, settler, FACTORING_FEE_BPS, ADVANCE_BPS);
        vm.prank(admin);
        v.setSettlementModule(address(f));
        vm.prank(admin);
        f.setPayeeAllowlist(payee1, true);
        rt.mint(buyer, 5_000_000);
        rt.mint(funder, 5_000_000);
        vm.prank(buyer);
        rt.approve(address(v), type(uint256).max);
        vm.prank(funder);
        rt.approve(address(f), type(uint256).max);
        vm.prank(funder);
        f.fundLiquidity(LIQUIDITY);
    }

    function _registerAndLockOn(SealEscrowVault v, bytes32 taskId) internal {
        SealAuthorization memory seal =
            _sealCustom(taskId, buyer, worker, GROSS, block.timestamp + 7 days, v.nonces(buyer));
        _registerWith(v, seal, buyerPk);
        v.lockEscrow(taskId);
    }

    /// @notice BLOCKED: a token hook firing during spendAdvance's transfer
    ///         cannot reenter spendAdvance — the ReentrancyGuard (and the
    ///         worker-only check) reject it, and the outer spend's
    ///         accounting stays exact.
    function testAdv_ReentrantToken_SpendReentry_Blocked() public {
        (ReentrantToken rt, SealEscrowVault v, SealFactoring f) = _deployReentrant();
        bytes32 taskId = keccak256("adv-reenter-spend");
        _registerAndLockOn(v, taskId);
        f.issueAdvance(taskId, 100_000);
        rt.arm(f, taskId, payee1, 1, bytes32(0));
        vm.prank(worker);
        f.spendAdvance(taskId, payee1, 10_000);
        assertFalse(rt.reentrySucceeded(), "reentrant spend was rejected");
        assertEq(rt.balanceOf(payee1), 10_000, "payee received exactly the outer spend");
        (,,,,, uint256 spent,) = f.advances(taskId);
        assertEq(spent, 10_000, "spent accounting uncorrupted");
    }

    /// @notice BLOCKED: a token hook firing during settle's escrow release
    ///         cannot reenter settle — the guard holds and the waterfall
    ///         completes exactly once.
    function testAdv_ReentrantToken_SettleReentry_Blocked() public {
        (ReentrantToken rt, SealEscrowVault v, SealFactoring f) = _deployReentrant();
        bytes32 taskId = keccak256("adv-reenter-settle");
        _registerAndLockOn(v, taskId);
        f.issueAdvance(taskId, 100_000);
        rt.arm(f, taskId, payee1, 2, keccak256("reentrant-sigil"));
        uint256 workerBefore = rt.balanceOf(worker);
        _attestOn(f, taskId, keccak256("sigil"));
        vm.prank(settler);
        f.settle(taskId, keccak256("sigil"));
        assertFalse(rt.reentrySucceeded(), "reentrant settle was rejected");
        assertEq(rt.balanceOf(treasury), 50_000, "platform fee paid exactly once");
        assertEq(rt.balanceOf(worker), workerBefore + 950_000, "remainder paid exactly once");
        assertEq(rt.balanceOf(address(v)), 0, "vault drained exactly once");
    }

    // =====================================================================
    // FIX-PASS REGRESSION TESTS (new — one per mechanism)
    // =====================================================================

    /// @notice FIX (finding 3, second line of defense): once a default has
    ///         booked clawback debt against a worker, that worker cannot
    ///         draw ANY new advance — the recycle path is gated even for
    ///         ordinary (non-self-dealing) seals.
    function testFix_DebtGate_BlocksNewAdvanceAfterDefault() public {
        bytes32 t1 = keccak256("fix-debt-1");
        SealAuthorization memory seal1 = _registerAndLock(t1);
        factoring.issueAdvance(t1, 100_000);
        vm.prank(worker);
        factoring.spendAdvance(t1, payee1, 100_000);
        vm.warp(seal1.deadline + 1);
        factoring.expire(t1);
        uint256 debt = factoring.clawbackDebtOf(worker);
        assertEq(debt, 100_200, "debt booked: 100k spent + 200 fee, no bond");

        bytes32 t2 = keccak256("fix-debt-2");
        _registerAndLock(t2);
        vm.expectRevert(
            abi.encodeWithSelector(SealFactoring.OutstandingClawbackDebt.selector, worker, debt)
        );
        factoring.issueAdvance(t2, 100_000);
    }

    /// @notice FIX (finding 2): the timelock lifecycle itself — execution
    ///         before the delay reverts; after the delay, with nothing
    ///         locked, the change applies.
    function testFix_ModuleChange_TimelockLifecycle_NoActiveEscrow() public {
        address newModule = address(0x777);
        vm.prank(admin);
        vault.setSettlementModule(newModule); // proposes (module already wired)
        uint256 effectiveAt = vault.pendingModuleEffectiveAt();
        vm.prank(admin);
        vm.expectRevert(
            abi.encodeWithSelector(SealEscrowVault.ModuleChangeNotReady.selector, effectiveAt, block.timestamp)
        );
        vault.executeSettlementModuleChange();
        assertEq(vault.settlementModule(), address(factoring), "old module still in place during delay");
        vm.warp(block.timestamp + vault.MODULE_CHANGE_DELAY() + 1);
        vm.prank(admin);
        vault.executeSettlementModuleChange();
        assertEq(vault.settlementModule(), newModule, "change applies after delay with no escrow at risk");
        assertEq(vault.pendingSettlementModule(), address(0), "pending cleared");
    }

    /// @notice FIX (finding 5, failure path): the snapshot also governs the
    ///         failure waterfall — a mid-flight fee hike cannot grow the
    ///         amount owed (and thus the booked debt) after the fact.
    function testFix_FeeSnapshot_FailurePathUsesIssuanceFee() public {
        bytes32 taskId = keccak256("fix-fee-fail");
        _registerAndLock(taskId);
        factoring.issueAdvance(taskId, 200_000);
        vm.prank(worker);
        factoring.spendAdvance(taskId, payee1, 100_000);
        vm.prank(admin);
        factoring.setFactoringFeeBps(500); // hike mid-flight
        vm.prank(buyer);
        factoring.markFailed(taskId); // dispute
        vm.warp(block.timestamp + factoring.DISPUTE_WINDOW() + 1);
        factoring.finalizeFailure(taskId);
        // Owed at the SNAPSHOT 20 bps: 100_000 + 200 = 100_200 (not 105_000).
        assertEq(factoring.clawbackDebtOf(worker), 100_200, "failure owed uses issuance fee snapshot");
    }

    /// @notice FIX (finding 7): the exact boundary second — at
    ///         block.timestamp == deadline, settle is still valid and expire
    ///         still reverts; one second later the rule flips completely.
    function testFix_DeadlineBoundary_ExactSecond() public {
        bytes32 tA = keccak256("fix-boundary-a");
        SealAuthorization memory sealA =
            _sealCustom(tA, buyer, worker, GROSS, block.timestamp + 100, vault.nonces(buyer));
        _registerWith(vault, sealA, buyerPk);
        vault.lockEscrow(tA);
        factoring.issueAdvance(tA, 100_000);

        bytes32 tB = keccak256("fix-boundary-b");
        SealAuthorization memory sealB =
            _sealCustom(tB, buyer, worker, GROSS, block.timestamp + 100, vault.nonces(buyer));
        _registerWith(vault, sealB, buyerPk);
        vault.lockEscrow(tB);
        factoring.issueAdvance(tB, 100_000);

        vm.warp(sealA.deadline); // exactly at the deadline
        vm.prank(stranger);
        vm.expectRevert(SealFactoring.TaskNotYetExpired.selector);
        factoring.expire(tA);
        _attestAndSettle(tA, keccak256("sigil")); // settle valid at the deadline second
        assertEq(uint256(_state(tA)), uint256(SealFactoring.AdvanceState.Settled));

        vm.warp(sealB.deadline + 1); // one second past
        _attest(tB, keccak256("sigil"));
        vm.prank(settler);
        vm.expectRevert(SealFactoring.SealExpired.selector);
        factoring.settle(tB, keccak256("sigil"));
        vm.prank(stranger);
        factoring.expire(tB);
        assertEq(uint256(_state(tB)), uint256(SealFactoring.AdvanceState.Defaulted));
    }

    /// @notice FIX (finding 1): role separation is enforced — the verifier
    ///         can never be the settler (either direction), the settler
    ///         cannot attest, and the verifier cannot settle.
    function testFix_RoleSeparation_VerifierAndSettlerDistinct() public {
        assertEq(factoring.verifier(), admin, "verifier defaults to admin, distinct from settler");
        assertTrue(factoring.verifier() != factoring.settler(), "roles distinct");
        vm.prank(admin);
        vm.expectRevert(SealFactoring.RolesMustBeSeparate.selector);
        factoring.setVerifier(settler);
        vm.prank(admin);
        vm.expectRevert(SealFactoring.RolesMustBeSeparate.selector);
        factoring.setSettler(admin); // admin IS the verifier

        bytes32 taskId = keccak256("fix-roles");
        _registerAndLock(taskId);
        factoring.issueAdvance(taskId, 100_000);
        vm.prank(settler); // settler cannot attest its own settlement
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.attestVerification(taskId, keccak256("sigil"));
        _attest(taskId, keccak256("sigil"));
        vm.prank(admin); // verifier (admin) cannot execute settlement
        vm.expectRevert(SealFactoring.Unauthorized.selector);
        factoring.settle(taskId, keccak256("sigil"));
    }

    /// @notice FIX (finding 8): the minimum-advance boundary — MIN-1
    ///         reverts, exactly MIN succeeds.
    function testFix_MinAdvance_Boundary() public {
        bytes32 taskId = keccak256("fix-min-advance");
        _registerAndLock(taskId);
        uint256 minAdv = factoring.MIN_ADVANCE();
        vm.expectRevert(
            abi.encodeWithSelector(SealFactoring.AdvanceBelowMinimum.selector, minAdv - 1, minAdv)
        );
        factoring.issueAdvance(taskId, minAdv - 1);
        factoring.issueAdvance(taskId, minAdv);
        (,,, uint256 creditIssued,,) = _adv(taskId);
        assertEq(creditIssued, minAdv, "minimum-size advance issues");
    }

    /// @notice FIX (findings 1/6): an uncontested buyer dispute finalizes
    ///         after the window with the exact pre-fix clawback math, and a
    ///         second dispute on the same advance is impossible.
    function testFix_DisputeFinalize_Uncontested_ExactMath() public {
        bytes32 taskId = keccak256("fix-dispute-finalize");
        _registerAndLock(taskId);
        vm.prank(worker);
        factoring.postBond(30_000);
        factoring.issueAdvance(taskId, 200_000);
        vm.prank(worker);
        factoring.spendAdvance(taskId, payee1, 100_000);
        vm.prank(buyer);
        factoring.markFailed(taskId); // dispute opens
        vm.prank(buyer);
        vm.expectRevert(SealFactoring.FailureAlreadyDisputed.selector);
        factoring.markFailed(taskId);
        uint256 buyerBefore = token.balanceOf(buyer);
        vm.warp(block.timestamp + factoring.DISPUTE_WINDOW() + 1);
        vm.prank(stranger); // anyone may finalize an uncontested dispute
        factoring.finalizeFailure(taskId);
        assertEq(token.balanceOf(buyer), buyerBefore + GROSS, "buyer refunded after uncontested window");
        // owed = 100_000 + 200 snapshot fee = 100_200; bond 30_000 seized.
        assertEq(factoring.bondOf(worker), 0, "bond seized at finalization");
        assertEq(factoring.clawbackDebtOf(worker), 100_200 - 30_000, "residual debt exact");
        assertEq(uint256(_state(taskId)), uint256(SealFactoring.AdvanceState.Defaulted));
    }
}
