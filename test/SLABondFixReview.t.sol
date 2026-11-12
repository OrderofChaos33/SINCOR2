// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {SLABondManager, ISigilHubView} from "../contracts/sla/SLABondManager.sol";

// ============================================================================
// INDEPENDENT FIX-PASS RE-REVIEW - SLABondManager (Workload 2, Lane 5)
// Written by the adversarial re-reviewer (not the fix author). Probes attack
// the FIX mechanisms themselves: (F1) escapeHubVerifiedBond and (F2) the
// postBond hirer==worker block. Tests assert the SECURE behavior; a failure
// because an attack succeeded would be a FINDING.
// ============================================================================

contract ReviewBondToken {
    uint8 public constant decimals = 6;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amt) external { balanceOf[to] += amt; }
    function approve(address spender, uint256 amt) external returns (bool) {
        allowance[msg.sender][spender] = amt;
        return true;
    }
    function transfer(address to, uint256 amt) external returns (bool) {
        _move(msg.sender, to, amt);
        return true;
    }
    function transferFrom(address from, address to, uint256 amt) external returns (bool) {
        uint256 a = allowance[from][msg.sender];
        require(a >= amt, "allowance");
        if (a != type(uint256).max) allowance[from][msg.sender] = a - amt;
        _move(from, to, amt);
        return true;
    }
    function _move(address from, address to, uint256 amt) internal {
        require(balanceOf[from] >= amt, "balance");
        balanceOf[from] -= amt;
        balanceOf[to] += amt;
    }
}

/// @notice Settable Hub stub with an optional revert mode (fail-closed probe).
contract ReviewHub is ISigilHubView {
    bool public verified;
    bool public reverts;

    function setVerified(bool v) external { verified = v; }
    function setReverts(bool r) external { reverts = r; }

    function isTaskVerified(bytes32, bytes32) external view returns (bool) {
        require(!reverts, "hub down");
        return verified;
    }
}

contract SLABondFixReviewTest is Test {
    ReviewBondToken public usdc;
    ReviewHub public hub;

    address public worker = address(0x1001);
    address public workerB = address(0x1002);
    address public hirer = address(0x2001);
    address public treasury = address(0x3001);
    address public anyone = address(0x4001);

    bytes32 public taskId = bytes32(uint256(0xB001));
    bytes32 public taskId2 = bytes32(uint256(0xB002));
    bytes32 public sigil = keccak256("review-sigil-1");

    uint256 internal constant BOND = 25 * 1e6;
    uint256 internal constant START = 1_000 * 1e6;

    function setUp() public {
        usdc = new ReviewBondToken();
        hub = new ReviewHub();
        usdc.mint(worker, START * 4); // headroom for repost probes
        usdc.mint(workerB, START);
    }

    function _hubMgr() internal returns (SLABondManager m) {
        m = new SLABondManager(address(usdc), treasury, address(hub), 10_000);
        vm.prank(worker);
        usdc.approve(address(m), type(uint256).max);
        vm.prank(workerB);
        usdc.approve(address(m), type(uint256).max);
    }

    function _plainMgr() internal returns (SLABondManager m) {
        m = new SLABondManager(address(usdc), treasury, address(0), 10_000);
        vm.prank(worker);
        usdc.approve(address(m), type(uint256).max);
    }

    function _postOn(SLABondManager m, bytes32 t, bytes32 expected) internal returns (uint256 deadline) {
        deadline = block.timestamp + 1 days;
        vm.prank(worker);
        m.postBond(t, hirer, BOND, deadline, expected);
    }

    function _escapeTime(SLABondManager m, bytes32 t, address w) internal view returns (uint256) {
        SLABondManager.Bond memory b = m.getBond(t, w);
        return b.deadline + m.HUB_ESCAPE_WINDOW();
    }

    // =====================================================================
    // F1 - ESCAPE WINDOW BOUNDARY (exact second)
    // =====================================================================

    function testReview_Escape_ExactBoundaryShut_PlusOneSecondOpen() public {
        SLABondManager m = _hubMgr();
        uint256 deadline = _postOn(m, taskId, sigil);
        hub.setVerified(true);
        uint256 availableAt = deadline + m.HUB_ESCAPE_WINDOW();

        vm.warp(availableAt); // exactly deadline + window: STILL SHUT
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.EscapeWindowNotReached.selector, availableAt, availableAt)
        );
        m.escapeHubVerifiedBond(taskId, worker);
        SLABondManager.Bond memory b = m.getBond(taskId, worker);
        assertEq(uint256(b.state), uint256(SLABondManager.BondState.Active), "no state change at the boundary");
        assertEq(usdc.balanceOf(worker), START * 4 - BOND, "nothing paid at the boundary");

        vm.warp(availableAt + 1); // +1s: OPEN (hub still verified)
        m.escapeHubVerifiedBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START * 4, "bond back in full one second past the window");
    }

    // =====================================================================
    // F1 - ESCAPE PREDICATE: unpinned / hub-unverified / no-hub, far future
    // =====================================================================

    function testReview_Escape_HubUnverified_FarFuture_NeverOpens_SlashStillWorks() public {
        SLABondManager m = _hubMgr();
        uint256 deadline = _postOn(m, taskId, sigil);
        hub.setVerified(false); // genuine miss
        vm.warp(deadline + m.HUB_ESCAPE_WINDOW() + 3650 days); // ~10 years past the window
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.EscapeNotHubVerified.selector, taskId, worker)
        );
        m.escapeHubVerifiedBond(taskId, worker);
        // The failed escape changed nothing: the legitimate slash still pays.
        m.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), BOND);
        assertEq(usdc.balanceOf(worker), START * 4 - BOND);
    }

    function testReview_Escape_UnpinnedBond_FarFuture_NeverOpens() public {
        SLABondManager m = _hubMgr();
        uint256 deadline = _postOn(m, taskId, bytes32(0)); // UNPINNED
        hub.setVerified(true); // hub reports verified - predicate still false (no pin)
        vm.warp(deadline + m.HUB_ESCAPE_WINDOW() + 3650 days);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.EscapeNotHubVerified.selector, taskId, worker)
        );
        m.escapeHubVerifiedBond(taskId, worker);
        m.slashBond(taskId, worker); // unpinned: hub never vetoed this slash either
        assertEq(usdc.balanceOf(hirer), BOND);
    }

    function testReview_Escape_NoHubBond_FarFuture_NeverOpens() public {
        SLABondManager m = _plainMgr(); // sigilHub == address(0)
        uint256 deadline = _postOn(m, taskId, sigil); // pinned, but no hub exists
        vm.warp(deadline + m.HUB_ESCAPE_WINDOW() + 3650 days);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.EscapeNotHubVerified.selector, taskId, worker)
        );
        m.escapeHubVerifiedBond(taskId, worker);
        m.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), BOND);
    }

    function testReview_Escape_RevertingHub_FailsClosed() public {
        SLABondManager m = _hubMgr();
        uint256 deadline = _postOn(m, taskId, sigil);
        hub.setVerified(true);
        hub.setReverts(true); // hub breaks AFTER verifying
        vm.warp(deadline + m.HUB_ESCAPE_WINDOW() + 1);
        vm.expectRevert(); // hub revert propagates: escape fails closed
        m.escapeHubVerifiedBond(taskId, worker);
        vm.expectRevert(); // and the slash fails closed too - no unchecked exit
        m.slashBond(taskId, worker);
        SLABondManager.Bond memory b = m.getBond(taskId, worker);
        assertEq(uint256(b.state), uint256(SLABondManager.BondState.Active), "bond intact");
        assertEq(usdc.balanceOf(address(m)), BOND, "funds locked, not lost or paid");
        assertEq(usdc.balanceOf(worker), START * 4 - BOND);
        assertEq(usdc.balanceOf(hirer), 0);
    }

    // =====================================================================
    // F1 - HUB FLIPS: predicate is evaluated AT ESCAPE TIME
    // =====================================================================

    function testReview_Escape_HubCorrectedDuringWindow_KillsEscape_SlashOpens() public {
        SLABondManager m = _hubMgr();
        uint256 deadline = _postOn(m, taskId, sigil);
        hub.setVerified(true); // wrong record initially: slash vetoed
        vm.warp(deadline + 1);
        vm.expectRevert(abi.encodeWithSelector(SLABondManager.BondVerified.selector, taskId, worker));
        m.slashBond(taskId, worker);
        hub.setVerified(false); // record corrected during the escape window
        vm.warp(deadline + m.HUB_ESCAPE_WINDOW() + 1);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.EscapeNotHubVerified.selector, taskId, worker)
        );
        m.escapeHubVerifiedBond(taskId, worker); // escape follows the CURRENT predicate
        m.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), BOND, "corrected record restores the legitimate slash");
    }

    /// @notice Behavior probe (design consequence, asserted as-is): if the
    ///         Hub verifies only AFTER the window and nobody slashed in the
    ///         meantime, the escape opens at that later moment - the
    ///         predicate is checked at escape time, not latched at the
    ///         window's end.
    function testReview_Escape_HubVerifiesLate_OpensEscape() public {
        SLABondManager m = _hubMgr();
        uint256 deadline = _postOn(m, taskId, sigil);
        hub.setVerified(false); // slashable throughout the window...
        vm.warp(deadline + m.HUB_ESCAPE_WINDOW() + 1);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.EscapeNotHubVerified.selector, taskId, worker)
        );
        m.escapeHubVerifiedBond(taskId, worker);
        hub.setVerified(true); // ...but the hirer never slashed, and now the hub verifies
        m.escapeHubVerifiedBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START * 4, "worker recovered the bond once hub-verified");
        assertEq(usdc.balanceOf(hirer), 0);
    }

    // =====================================================================
    // F1 - DOUBLE-PAY MATRIX (escape vs every other exit)
    // =====================================================================

    function testReview_DoublePay_ReleaseThenEscape_Reverts() public {
        SLABondManager m = _hubMgr();
        uint256 deadline = _postOn(m, taskId, sigil);
        vm.prank(worker);
        m.submitSigil(taskId, sigil); // on-time internal SIGIL
        hub.setVerified(true);
        m.releaseBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START * 4);
        vm.warp(deadline + m.HUB_ESCAPE_WINDOW() + 1);
        vm.expectRevert(abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker));
        m.escapeHubVerifiedBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START * 4, "released bond paid exactly once");
        assertEq(usdc.balanceOf(address(m)), 0);
    }

    function testReview_DoublePay_SlashThenEscape_Reverts() public {
        SLABondManager m = _hubMgr();
        uint256 deadline = _postOn(m, taskId, sigil);
        hub.setVerified(false);
        vm.warp(deadline + 1);
        m.slashBond(taskId, worker);
        hub.setVerified(true); // hub flips AFTER the slash - too late
        vm.warp(deadline + m.HUB_ESCAPE_WINDOW() + 1);
        vm.expectRevert(abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker));
        m.escapeHubVerifiedBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), BOND, "hirer kept the slash");
        assertEq(usdc.balanceOf(worker), START * 4 - BOND, "worker never recovers a slashed bond");
    }

    function testReview_DoublePay_EscapeThenSlash_Reverts() public {
        SLABondManager m = _hubMgr();
        _postOn(m, taskId, sigil);
        hub.setVerified(true);
        vm.warp(_escapeTime(m, taskId, worker) + 1);
        m.escapeHubVerifiedBond(taskId, worker);
        vm.expectRevert(abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker));
        m.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), 0, "hirer cannot slash an escaped bond");
        assertEq(usdc.balanceOf(worker), START * 4);
    }

    function testReview_DoublePay_EscapeThenEscape_AndEscapeThenRelease_Revert() public {
        SLABondManager m = _hubMgr();
        _postOn(m, taskId, sigil);
        hub.setVerified(true);
        vm.warp(_escapeTime(m, taskId, worker) + 1);
        m.escapeHubVerifiedBond(taskId, worker);
        vm.expectRevert(abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker));
        m.escapeHubVerifiedBond(taskId, worker);
        vm.expectRevert(abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker));
        m.releaseBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START * 4, "escaped bond paid exactly once across all exits");
        assertEq(usdc.balanceOf(address(m)), 0);
    }

    function testReview_Escape_WhenInternalSigilPresent_PaysOnce() public {
        // Worker DID submit on time AND the hub verified: release is the
        // normal path, but if nobody releases for 30+ days the escape must
        // still pay the worker exactly once (same payee, same amount).
        SLABondManager m = _hubMgr();
        _postOn(m, taskId, sigil);
        vm.prank(worker);
        m.submitSigil(taskId, sigil);
        hub.setVerified(true);
        vm.warp(_escapeTime(m, taskId, worker) + 1);
        m.escapeHubVerifiedBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START * 4);
        vm.expectRevert(abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker));
        m.releaseBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START * 4, "no second payout via release");
    }

    function testReview_Escape_ByThirdParty_PaysWorkerOnly() public {
        SLABondManager m = _hubMgr();
        _postOn(m, taskId, sigil);
        hub.setVerified(true);
        vm.warp(_escapeTime(m, taskId, worker) + 1);
        uint256 anyoneBefore = usdc.balanceOf(anyone);
        vm.prank(anyone); // third party triggers
        m.escapeHubVerifiedBond(taskId, worker);
        assertEq(usdc.balanceOf(anyone), anyoneBefore, "caller gains nothing");
        assertEq(usdc.balanceOf(worker), START * 4, "payee is the bonded worker, in full");
        assertEq(usdc.balanceOf(hirer), 0, "hirer gains nothing from an escape");
    }

    // =====================================================================
    // F1 - HUB / PIN MUTABILITY: nothing can be re-pointed after posting
    // =====================================================================

    function testReview_HubAndPin_ImmutableAcrossLifecycle() public {
        SLABondManager m = _hubMgr();
        address hubAtDeploy = m.sigilHub();
        uint256 deadline = _postOn(m, taskId, sigil);
        SLABondManager.Bond memory b0 = m.getBond(taskId, worker);
        assertEq(b0.expectedSigil, sigil, "pin recorded at posting");
        // Exercise the lifecycle; neither the hub pointer nor the pin moves.
        vm.prank(worker);
        m.submitSigil(taskId, sigil);
        m.releaseBond(taskId, worker);
        SLABondManager.Bond memory b1 = m.getBond(taskId, worker);
        assertEq(m.sigilHub(), hubAtDeploy, "hub fixed at construction - no setter exists");
        assertEq(m.sigilHub(), address(hub));
        assertEq(b1.expectedSigil, sigil, "pin unchanged by submit/release");
        assertEq(b1.deadline, deadline, "deadline unchanged");
        // A fresh bond on a second manager points at ITS OWN hub, unaffected.
        SLABondManager m2 = _plainMgr();
        assertEq(m2.sigilHub(), address(0), "hub is per-deployment constructor state");
    }

    // =====================================================================
    // F2 - NO ALTERNATE CREATION PATH; repost hygiene
    // =====================================================================

    function testReview_NoAlternateCreationPath_GhostBondHasNoExits() public {
        SLABondManager m = _hubMgr();
        // Nothing was ever posted for (taskId, worker): every state-changing
        // entry point keyed to a bond reverts, and the record stays None.
        vm.expectRevert(abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker));
        m.releaseBond(taskId, worker);
        vm.expectRevert(abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker));
        m.slashBond(taskId, worker);
        vm.expectRevert(abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker));
        m.escapeHubVerifiedBond(taskId, worker);
        vm.prank(worker);
        vm.expectRevert(abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker));
        m.submitSigil(taskId, sigil);
        SLABondManager.Bond memory b = m.getBond(taskId, worker);
        assertEq(uint256(b.state), uint256(SLABondManager.BondState.None), "no ghost bond conjured");
        assertEq(usdc.balanceOf(address(m)), 0);
        // postBond is the ONLY creation path, and its F2 gate applies there:
        vm.prank(worker);
        vm.expectRevert(SLABondManager.HirerEqualsWorker.selector);
        m.postBond(taskId, worker, BOND, block.timestamp + 1 days, bytes32(0));
    }

    function testReview_RepostAfterEscape_F2GateApplies_FreshBondClean() public {
        SLABondManager m = _hubMgr();
        _postOn(m, taskId, sigil);
        hub.setVerified(true);
        vm.warp(_escapeTime(m, taskId, worker) + 1);
        m.escapeHubVerifiedBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START * 4, "first bond escaped in full");

        // Reposting the same (taskId, worker) still passes through the F2
        // gate - the terminal Escaped record is not a back door.
        vm.prank(worker);
        vm.expectRevert(SLABondManager.HirerEqualsWorker.selector);
        m.postBond(taskId, worker, BOND, block.timestamp + 1 days, bytes32(0));

        // A legitimate repost creates a FRESH bond: full amount pulled
        // again, no SIGIL carried over, deadline is the new one.
        uint256 deadline2 = block.timestamp + 2 days;
        vm.prank(worker);
        m.postBond(taskId, hirer, BOND, deadline2, sigil);
        SLABondManager.Bond memory b = m.getBond(taskId, worker);
        assertEq(uint256(b.state), uint256(SLABondManager.BondState.Active));
        assertEq(b.amount, BOND);
        assertEq(b.sigilCommitment, bytes32(0), "old record fully overwritten");
        assertEq(b.deadline, deadline2);
        assertEq(usdc.balanceOf(address(m)), BOND, "manager holds exactly the new bond");
        // The new bond settles on its own merits, exactly once.
        vm.prank(worker);
        m.submitSigil(taskId, sigil);
        m.releaseBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START * 4, "worker whole after both lifecycles, no double-pay");
        assertEq(usdc.balanceOf(address(m)), 0);
    }

    function testReview_F2_SelfDealingViaSecondWorkerKeyStillBlockedPerBond() public {
        // F2 is per-bond (hirer == msg.sender). A worker cannot dodge it by
        // naming a DIFFERENT worker address they also control as hirer -
        // wait, that IS a different address; the contract-level check is
        // msg.sender only. The deterrent-relevant case the fix targets is
        // the literal self-bond; here workerB posts naming workerB (itself)
        // and is blocked identically.
        SLABondManager m = _plainMgr();
        vm.prank(workerB);
        usdc.approve(address(m), type(uint256).max);
        vm.prank(workerB);
        vm.expectRevert(SLABondManager.HirerEqualsWorker.selector);
        m.postBond(taskId2, workerB, BOND, block.timestamp + 1 days, bytes32(0));
        assertEq(usdc.balanceOf(workerB), START, "no funds pulled for a self-dealing post");
        // And a bond where workerB genuinely works for the real hirer posts fine.
        vm.prank(workerB);
        m.postBond(taskId2, hirer, BOND, block.timestamp + 1 days, bytes32(0));
        assertEq(usdc.balanceOf(address(m)), BOND);
    }
}
