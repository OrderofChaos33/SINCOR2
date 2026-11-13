// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {SLABondManager, ISigilHubView} from "../contracts/sla/SLABondManager.sol";

/// @notice USDC-like mock (6 decimals). Transfer surface mirrors the
///         MockCollateral in StakeSlashManagerUnstakeClamp.t.sol — the SUT
///         only needs transfer/transferFrom, via low-level calls.
contract MockBondToken {
    uint8 public constant decimals = 6;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amt) external {
        balanceOf[to] += amt;
    }

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

/// @notice Stub SincorSettlementHub for the external-veto path: reports
///         isTaskVerified from a settable flag, Hub-shaped signature.
contract MockSigilHub is ISigilHubView {
    bool public verified;

    function setVerified(bool v) external {
        verified = v;
    }

    function isTaskVerified(bytes32, bytes32) external view returns (bool) {
        return verified;
    }
}

contract SLABondManagerTest is Test {
    MockBondToken public usdc;
    SLABondManager public mgr;
    MockSigilHub public hub;

    address public worker;
    address public workerB;
    address public hirer;
    address public treasury;
    address public anyone;

    bytes32 public taskId;
    bytes32 public taskId2;
    bytes32 public sigil = keccak256("sigil-output-1");
    bytes32 public sigilB = keccak256("sigil-output-2");

    uint256 internal constant BOND = 25 * 1e6; // $25.00 micro-bond, USDC 6dp
    uint256 internal constant START = 1_000 * 1e6;

    function setUp() public {
        worker = address(0x1001);
        workerB = address(0x1002);
        hirer = address(0x2001);
        treasury = address(0x3001);
        anyone = address(0x4001);
        taskId = bytes32(uint256(0xA001));
        taskId2 = bytes32(uint256(0xA002));

        usdc = new MockBondToken();
        hub = new MockSigilHub();
        // Default deployment: no external hub, 100% of slash to hirer.
        mgr = new SLABondManager(address(usdc), treasury, address(0), 10_000);

        for (uint256 i = 0; i < 2; i++) {
            address w = i == 0 ? worker : workerB;
            usdc.mint(w, START);
            vm.prank(w);
            usdc.approve(address(mgr), type(uint256).max);
        }
    }

    function _deadline() internal view returns (uint256) {
        return block.timestamp + 1 days;
    }

    function _post(address w, bytes32 t, bytes32 expected) internal returns (uint256 deadline) {
        deadline = _deadline();
        vm.prank(w);
        mgr.postBond(t, hirer, BOND, deadline, expected);
    }

    /// @dev Warp to just past a bond's stored deadline (target derived from
    ///      contract state, never from timestamp arithmetic in the warp
    ///      argument — see StakeSlashManagerUnstakeClamp.t.sol note).
    function _warpPastDeadline(SLABondManager m, bytes32 t, address w) internal {
        SLABondManager.Bond memory b = m.getBond(t, w);
        vm.warp(b.deadline + 1);
    }

    // --- posting -----------------------------------------------------------

    function test_PostBond_PullsBondAndRecordsBinding() public {
        uint256 deadline = _post(worker, taskId, bytes32(0));
        assertEq(usdc.balanceOf(worker), START - BOND);
        assertEq(usdc.balanceOf(address(mgr)), BOND);
        SLABondManager.Bond memory b = mgr.getBond(taskId, worker);
        assertEq(b.taskId, taskId);
        assertEq(b.worker, worker);
        assertEq(b.hirer, hirer);
        assertEq(b.amount, BOND);
        assertEq(b.deadline, deadline);
        assertEq(uint256(b.state), uint256(SLABondManager.BondState.Active));
        assertEq(mgr.taskWorker(taskId), worker);
    }

    function test_PostBond_RevertsZeroAmount() public {
        vm.prank(worker);
        vm.expectRevert(SLABondManager.InvalidAmount.selector);
        mgr.postBond(taskId, hirer, 0, _deadline(), bytes32(0));
    }

    function test_PostBond_RevertsPastDeadline() public {
        vm.prank(worker);
        vm.expectRevert(SLABondManager.InvalidDeadline.selector);
        mgr.postBond(taskId, hirer, BOND, block.timestamp, bytes32(0));
    }

    function test_PostBond_DoubleActiveBondReverts() public {
        _post(worker, taskId, bytes32(0));
        vm.prank(worker);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.BondAlreadyExists.selector, taskId, worker)
        );
        mgr.postBond(taskId, hirer, BOND, _deadline(), bytes32(0));
    }

    // --- FULL LOOP A: on-time SIGIL -> bond back in full ---------------------

    function test_FullLoop_OnTimeSigil_BondReturnedInFull() public {
        _post(worker, taskId, bytes32(0));
        vm.prank(worker);
        mgr.submitSigil(taskId, sigil);
        assertTrue(mgr.isBondVerified(taskId, worker, sigil));
        assertTrue(mgr.isTaskVerified(taskId, sigil));

        // Settlement trigger is permissionless; payee is the worker.
        vm.prank(anyone);
        mgr.releaseBond(taskId, worker);

        assertEq(usdc.balanceOf(worker), START, "bond back in full, no fee skimmed");
        assertEq(usdc.balanceOf(address(mgr)), 0);
        SLABondManager.Bond memory b = mgr.getBond(taskId, worker);
        assertEq(uint256(b.state), uint256(SLABondManager.BondState.Released));
    }

    // --- FULL LOOP B: deadline passes, no SIGIL -> slash to hirer ------------

    function test_FullLoop_DeadlinePassesNoSigil_SlashPaysHirerExactlyBond() public {
        _post(worker, taskId, bytes32(0));
        uint256 hirerBefore = usdc.balanceOf(hirer);

        _warpPastDeadline(mgr, taskId, worker);
        vm.prank(anyone); // permissionless trigger
        mgr.slashBond(taskId, worker);

        assertEq(usdc.balanceOf(hirer) - hirerBefore, BOND, "hirer gains exactly the bond");
        assertEq(usdc.balanceOf(worker), START - BOND, "worker does not get the bond back");
        assertEq(usdc.balanceOf(treasury), 0, "no treasury cut at the 100% default");
        assertEq(usdc.balanceOf(address(mgr)), 0);
        SLABondManager.Bond memory b = mgr.getBond(taskId, worker);
        assertEq(uint256(b.state), uint256(SLABondManager.BondState.Slashed));
    }

    // --- slash guards ---------------------------------------------------------

    function test_Slash_EarlyAttemptReverts() public {
        _post(worker, taskId, bytes32(0));
        vm.prank(anyone);
        vm.expectRevert(); // DeadlineNotPassed
        mgr.slashBond(taskId, worker);
    }

    function test_Slash_AtExactDeadlineStillReverts() public {
        uint256 deadline = _post(worker, taskId, bytes32(0));
        vm.warp(deadline); // boundary: expired iff now > deadline (Hub rule)
        vm.prank(anyone);
        vm.expectRevert();
        mgr.slashBond(taskId, worker);
    }

    function test_Slash_AfterValidOnTimeSigilReverts() public {
        _post(worker, taskId, bytes32(0));
        vm.prank(worker);
        mgr.submitSigil(taskId, sigil);
        _warpPastDeadline(mgr, taskId, worker);
        vm.prank(anyone);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.BondVerified.selector, taskId, worker)
        );
        mgr.slashBond(taskId, worker);
        // And the bond is still releasable afterwards.
        mgr.releaseBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START);
    }

    function test_Slash_DoubleSlashBlocked() public {
        _post(worker, taskId, bytes32(0));
        _warpPastDeadline(mgr, taskId, worker);
        mgr.slashBond(taskId, worker);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker)
        );
        mgr.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), BOND, "hirer paid once, exactly");
    }

    function test_Slash_NonexistentBondReverts() public {
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker)
        );
        mgr.slashBond(taskId, worker);
    }

    // --- release guards ---------------------------------------------------------

    function test_Release_WithoutSigilReverts() public {
        _post(worker, taskId, bytes32(0));
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.BondNotVerified.selector, taskId, worker)
        );
        mgr.releaseBond(taskId, worker);
    }

    function test_Release_DoubleClaimBlocked() public {
        _post(worker, taskId, bytes32(0));
        vm.prank(worker);
        mgr.submitSigil(taskId, sigil);
        mgr.releaseBond(taskId, worker);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker)
        );
        mgr.releaseBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START, "paid exactly once");
    }

    function test_Release_AfterDeadlineWithoutSigilReverts_ThenSlashWorks() public {
        _post(worker, taskId, bytes32(0));
        _warpPastDeadline(mgr, taskId, worker);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.BondNotVerified.selector, taskId, worker)
        );
        mgr.releaseBond(taskId, worker);
        mgr.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), BOND);
    }

    // --- SIGIL timing / binding ---------------------------------------------------

    function test_Sigil_LateSubmissionRevertsAndDoesNotRescueBond() public {
        _post(worker, taskId, bytes32(0));
        _warpPastDeadline(mgr, taskId, worker);
        vm.prank(worker);
        vm.expectRevert(); // DeadlinePassed — no grace, deadline is deadline
        mgr.submitSigil(taskId, sigil);
        assertFalse(mgr.isBondVerified(taskId, worker, sigil));
        mgr.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), BOND);
    }

    function test_Sigil_AtExactDeadlineIsOnTime() public {
        uint256 deadline = _post(worker, taskId, bytes32(0));
        vm.warp(deadline); // Hub boundary: expired iff now > deadline
        vm.prank(worker);
        mgr.submitSigil(taskId, sigil);
        assertTrue(mgr.isBondVerified(taskId, worker, sigil));
        mgr.releaseBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START);
    }

    function test_Sigil_ZeroReverts() public {
        _post(worker, taskId, bytes32(0));
        vm.prank(worker);
        vm.expectRevert(SLABondManager.SigilZero.selector);
        mgr.submitSigil(taskId, bytes32(0));
    }

    function test_Sigil_DoubleSubmissionReverts() public {
        _post(worker, taskId, bytes32(0));
        vm.prank(worker);
        mgr.submitSigil(taskId, sigil);
        vm.prank(worker);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.SigilAlreadySubmitted.selector, taskId, worker)
        );
        mgr.submitSigil(taskId, sigilB);
    }

    function test_Sigil_PinnedExpected_MismatchReverts_MatchReleases() public {
        _post(worker, taskId, sigil); // expectedSigil pinned at post time
        vm.prank(worker);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.SigilMismatch.selector, sigil, sigilB)
        );
        mgr.submitSigil(taskId, sigilB);
        vm.prank(worker);
        mgr.submitSigil(taskId, sigil);
        mgr.releaseBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START);
    }

    function test_IsTaskVerified_HubPatternSemantics() public {
        _post(worker, taskId, bytes32(0));
        assertFalse(mgr.isTaskVerified(taskId, sigil), "nothing recorded yet");
        vm.prank(worker);
        mgr.submitSigil(taskId, sigil);
        assertTrue(mgr.isTaskVerified(taskId, sigil), "recorded == expected");
        assertFalse(mgr.isTaskVerified(taskId, sigilB), "wrong expected != recorded");
        assertFalse(mgr.isTaskVerified(taskId2, sigil), "unknown task");
    }

    // --- wrong-worker isolation ------------------------------------------------------

    function test_WrongWorkerIsolation() public {
        // Same taskId, two different workers bond independently.
        _post(worker, taskId, bytes32(0));
        _post(workerB, taskId, bytes32(0));
        assertEq(usdc.balanceOf(address(mgr)), 2 * BOND);

        // WorkerB submits a SIGIL: it binds to B's bond only.
        vm.prank(workerB);
        mgr.submitSigil(taskId, sigilB);
        assertTrue(mgr.isBondVerified(taskId, workerB, sigilB));
        assertFalse(mgr.isBondVerified(taskId, worker, sigilB));
        assertFalse(mgr.isBondVerified(taskId, worker, sigil));

        // Worker (no SIGIL) cannot be released on B's commitment.
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.BondNotVerified.selector, taskId, worker)
        );
        mgr.releaseBond(taskId, worker);

        // A third party cannot submit a SIGIL into worker's bond: the
        // lookup is keyed by msg.sender, so `anyone` has no bond here.
        vm.prank(anyone);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, anyone)
        );
        mgr.submitSigil(taskId, sigil);

        // Deadline passes: slashing worker's bond leaves B's untouched.
        _warpPastDeadline(mgr, taskId, worker);
        mgr.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), BOND);
        SLABondManager.Bond memory bB = mgr.getBond(taskId, workerB);
        assertEq(uint256(bB.state), uint256(SLABondManager.BondState.Active));
        assertEq(bB.amount, BOND);

        // B's on-time SIGIL still protects and releases B's bond in full.
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.BondVerified.selector, taskId, workerB)
        );
        mgr.slashBond(taskId, workerB);
        mgr.releaseBond(taskId, workerB);
        assertEq(usdc.balanceOf(workerB), START);
        assertEq(usdc.balanceOf(address(mgr)), 0);
    }

    // --- external Hub veto --------------------------------------------------------------

    function test_HubVeto_BlocksSlashWhenHubReportsVerified() public {
        SLABondManager hubMgr = new SLABondManager(address(usdc), treasury, address(hub), 10_000);
        vm.prank(worker);
        usdc.approve(address(hubMgr), type(uint256).max);
        vm.prank(worker);
        hubMgr.postBond(taskId, hirer, BOND, _deadline(), sigil); // pinned expected

        // No internal SIGIL, but the Hub recorded the settlement commitment.
        hub.setVerified(true);
        _warpPastDeadline(hubMgr, taskId, worker);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.BondVerified.selector, taskId, worker)
        );
        hubMgr.slashBond(taskId, worker);
    }

    function test_HubVeto_NotVerified_SlashProceeds() public {
        SLABondManager hubMgr = new SLABondManager(address(usdc), treasury, address(hub), 10_000);
        vm.prank(worker);
        usdc.approve(address(hubMgr), type(uint256).max);
        vm.prank(worker);
        hubMgr.postBond(taskId, hirer, BOND, _deadline(), sigil);
        hub.setVerified(false);
        _warpPastDeadline(hubMgr, taskId, worker);
        hubMgr.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), BOND);
    }

    // --- slash split parameter (non-default = founder decision) ---------------------------

    function test_SlashSplit_NonDefault_SplitsHirerAndTreasury() public {
        SLABondManager splitMgr = new SLABondManager(address(usdc), treasury, address(0), 8_000);
        vm.prank(worker);
        usdc.approve(address(splitMgr), type(uint256).max);
        vm.prank(worker);
        splitMgr.postBond(taskId, hirer, BOND, _deadline(), bytes32(0));
        _warpPastDeadline(splitMgr, taskId, worker);
        splitMgr.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), (BOND * 8_000) / 10_000);
        assertEq(usdc.balanceOf(treasury), BOND - (BOND * 8_000) / 10_000);
    }

    // --- F2 fix: self-dealing bonds are rejected in-contract -----------------

    function test_PostBond_RevertsWhenHirerEqualsWorker() public {
        vm.prank(worker);
        vm.expectRevert(SLABondManager.HirerEqualsWorker.selector);
        mgr.postBond(taskId, worker, BOND, _deadline(), bytes32(0)); // hirer == worker
        assertEq(usdc.balanceOf(worker), START, "no funds pulled");
        SLABondManager.Bond memory b = mgr.getBond(taskId, worker);
        assertEq(uint256(b.state), uint256(SLABondManager.BondState.None));
        // A distinct hirer still posts fine afterwards.
        _post(worker, taskId, bytes32(0));
        assertEq(usdc.balanceOf(address(mgr)), BOND);
    }

    // --- F1 fix: timed escape for Hub-vetoed (stranded) bonds -----------------

    function test_HubEscape_AfterWindow_HubVerifiedBondReleasesToWorker() public {
        SLABondManager hubMgr = new SLABondManager(address(usdc), treasury, address(hub), 10_000);
        vm.prank(worker);
        usdc.approve(address(hubMgr), type(uint256).max);
        uint256 deadline = _deadline();
        vm.prank(worker);
        hubMgr.postBond(taskId, hirer, BOND, deadline, sigil); // pinned
        hub.setVerified(true); // Hub recorded the SIGIL; no internal submitSigil
        vm.warp(deadline + hubMgr.HUB_ESCAPE_WINDOW() + 1);
        vm.prank(anyone); // permissionless trigger, fixed payee = worker
        hubMgr.escapeHubVerifiedBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START, "bond back in full via the escape hatch");
        assertEq(usdc.balanceOf(address(hubMgr)), 0);
        SLABondManager.Bond memory b = hubMgr.getBond(taskId, worker);
        assertEq(uint256(b.state), uint256(SLABondManager.BondState.Escaped));
    }

    function test_HubEscape_BeforeWindowReverts_AndSlashStaysVetoed() public {
        SLABondManager hubMgr = new SLABondManager(address(usdc), treasury, address(hub), 10_000);
        vm.prank(worker);
        usdc.approve(address(hubMgr), type(uint256).max);
        uint256 deadline = _deadline();
        vm.prank(worker);
        hubMgr.postBond(taskId, hirer, BOND, deadline, sigil);
        hub.setVerified(true);
        _warpPastDeadline(hubMgr, taskId, worker);
        vm.expectRevert(
            abi.encodeWithSelector(
                SLABondManager.EscapeWindowNotReached.selector,
                deadline + hubMgr.HUB_ESCAPE_WINDOW(),
                block.timestamp
            )
        );
        hubMgr.escapeHubVerifiedBond(taskId, worker);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.BondVerified.selector, taskId, worker)
        );
        hubMgr.slashBond(taskId, worker); // veto intact throughout the window
    }

    function test_HubEscape_WorkerCannotEscapeSlashableBond() public {
        SLABondManager hubMgr = new SLABondManager(address(usdc), treasury, address(hub), 10_000);
        vm.prank(worker);
        usdc.approve(address(hubMgr), type(uint256).max);
        uint256 deadline = _deadline();
        vm.prank(worker);
        hubMgr.postBond(taskId, hirer, BOND, deadline, sigil);
        hub.setVerified(false); // genuine miss - no Hub verification
        vm.warp(deadline + hubMgr.HUB_ESCAPE_WINDOW() + 1);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.EscapeNotHubVerified.selector, taskId, worker)
        );
        hubMgr.escapeHubVerifiedBond(taskId, worker);
        hubMgr.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), BOND, "legitimate slash unaffected by the hatch");
        assertEq(usdc.balanceOf(worker), START - BOND);
    }

    function test_Constructor_RevertsOnBadSplitConfig() public {
        vm.expectRevert(SLABondManager.InvalidConfig.selector);
        new SLABondManager(address(usdc), treasury, address(0), 10_001);
        vm.expectRevert(SLABondManager.InvalidConfig.selector);
        new SLABondManager(address(usdc), address(0), address(0), 8_000);
        vm.expectRevert(SLABondManager.InvalidConfig.selector);
        new SLABondManager(address(0), treasury, address(0), 10_000);
    }
}
