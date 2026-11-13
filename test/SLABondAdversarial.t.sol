// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {SLABondManager, ISigilHubView} from "../contracts/sla/SLABondManager.sol";

// ============================================================================
// ADVERSARIAL REVIEW - SLABondManager (Workload 2, Lane 5)
// Written by the adversarial reviewer; the contract under review is untouched.
// Every mock below exists to break the slash/release money path.
// ============================================================================

/// @notice Plain USDC-like token (same surface as the lane's MockBondToken).
contract AdvToken {
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

/// @notice Token that re-enters the manager from inside transfer /
///         transferFrom (ERC-777-style hook / malicious token behaviour).
///         Records whether any inner call succeeded - the reviewer asserts
///         the hook actually fired (hookCount > 0) so the test is not vacuous.
contract ReentrantToken {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    SLABondManager public mgr;
    bytes32 public atkTask;
    address public atkWorker;
    bool public armed;
    bool public busy;
    uint256 public hookCount;
    bool public innerReleaseOk;
    bool public innerSlashOk;
    bool public innerSubmitOk;
    bool public innerEscapeOk;

    function mint(address to, uint256 amt) external {
        balanceOf[to] += amt;
    }

    function approve(address spender, uint256 amt) external returns (bool) {
        allowance[msg.sender][spender] = amt;
        return true;
    }

    function setTarget(SLABondManager m, bytes32 task, address worker) external {
        mgr = m;
        atkTask = task;
        atkWorker = worker;
    }

    function arm() external {
        armed = true;
    }

    function _hook() internal {
        if (!armed || busy || address(mgr) == address(0)) return;
        busy = true;
        hookCount++;
        try mgr.releaseBond(atkTask, atkWorker) {
            innerReleaseOk = true;
        } catch {}
        try mgr.slashBond(atkTask, atkWorker) {
            innerSlashOk = true;
        } catch {}
        try mgr.submitSigil(atkTask, bytes32(uint256(0xBEEF))) {
            innerSubmitOk = true;
        } catch {}
        try mgr.escapeHubVerifiedBond(atkTask, atkWorker) {
            innerEscapeOk = true;
        } catch {}
        busy = false;
    }

    function transfer(address to, uint256 amt) external returns (bool) {
        _move(msg.sender, to, amt);
        _hook();
        return true;
    }

    function transferFrom(address from, address to, uint256 amt) external returns (bool) {
        uint256 a = allowance[from][msg.sender];
        require(a >= amt, "allowance");
        if (a != type(uint256).max) allowance[from][msg.sender] = a - amt;
        _move(from, to, amt);
        _hook();
        return true;
    }

    function _move(address from, address to, uint256 amt) internal {
        require(balanceOf[from] >= amt, "balance");
        balanceOf[from] -= amt;
        balanceOf[to] += amt;
    }
}

/// @notice Fee-on-transfer token: transferFrom debits the full amount but
///         credits only 90% (10% "fee" burned). Violates the contract's
///         documented token assumption - the test confirms what breaks.
contract FeeOnTransferToken {
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
        require(balanceOf[msg.sender] >= amt, "balance");
        balanceOf[msg.sender] -= amt;
        balanceOf[to] += amt;
        return true;
    }

    function transferFrom(address from, address to, uint256 amt) external returns (bool) {
        uint256 a = allowance[from][msg.sender];
        require(a >= amt, "allowance");
        if (a != type(uint256).max) allowance[from][msg.sender] = a - amt;
        require(balanceOf[from] >= amt, "balance");
        balanceOf[from] -= amt;
        balanceOf[to] += (amt * 90) / 100; // 10% fee vanishes
        return true;
    }
}

/// @notice Token whose transfers can be toggled to return false (no move).
///         Exercises _safeTransfer's return-value check + tx atomicity.
contract FalseReturnToken {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    bool public transfersEnabled = true;

    function setTransfersEnabled(bool v) external {
        transfersEnabled = v;
    }

    function mint(address to, uint256 amt) external {
        balanceOf[to] += amt;
    }

    function approve(address spender, uint256 amt) external returns (bool) {
        allowance[msg.sender][spender] = amt;
        return true;
    }

    function transfer(address to, uint256 amt) external returns (bool) {
        if (!transfersEnabled) return false;
        require(balanceOf[msg.sender] >= amt, "balance");
        balanceOf[msg.sender] -= amt;
        balanceOf[to] += amt;
        return true;
    }

    function transferFrom(address from, address to, uint256 amt) external returns (bool) {
        if (!transfersEnabled) return false;
        uint256 a = allowance[from][msg.sender];
        require(a >= amt, "allowance");
        if (a != type(uint256).max) allowance[from][msg.sender] = a - amt;
        require(balanceOf[from] >= amt, "balance");
        balanceOf[from] -= amt;
        balanceOf[to] += amt;
        return true;
    }
}

/// @notice Settable Hub stub (same shape as the lane's MockSigilHub).
contract AdvHub is ISigilHubView {
    bool public verified;

    function setVerified(bool v) external {
        verified = v;
    }

    function isTaskVerified(bytes32, bytes32) external view returns (bool) {
        return verified;
    }
}

/// @notice Hub that always reverts - a broken/malicious Hub integration.
contract RevertingHub is ISigilHubView {
    function isTaskVerified(bytes32, bytes32) external pure returns (bool) {
        revert("hub down");
    }
}

contract SLABondAdversarialTest is Test {
    AdvToken public usdc;
    SLABondManager public mgr;

    address public worker;
    address public workerB;
    address public workerC;
    address public hirer;
    address public treasury;
    address public anyone;

    bytes32 public taskId;
    bytes32 public taskId2;
    bytes32 public taskId3;
    bytes32 public taskId4;
    bytes32 public sigil = keccak256("sigil-output-1");
    bytes32 public sigilB = keccak256("sigil-output-2");
    bytes32 public junkSigil = bytes32(uint256(0xDEAD));

    uint256 internal constant BOND = 25 * 1e6;
    uint256 internal constant START = 1_000 * 1e6;

    function setUp() public {
        worker = address(0x1001);
        workerB = address(0x1002);
        workerC = address(0x1003);
        hirer = address(0x2001);
        treasury = address(0x3001);
        anyone = address(0x4001);
        taskId = bytes32(uint256(0xA001));
        taskId2 = bytes32(uint256(0xA002));
        taskId3 = bytes32(uint256(0xA003));
        taskId4 = bytes32(uint256(0xA004));

        usdc = new AdvToken();
        mgr = new SLABondManager(address(usdc), treasury, address(0), 10_000);

        address[3] memory ws = [worker, workerB, workerC];
        for (uint256 i = 0; i < 3; i++) {
            usdc.mint(ws[i], START);
            vm.prank(ws[i]);
            usdc.approve(address(mgr), type(uint256).max);
        }
    }

    function _post(address w, bytes32 t, bytes32 expected) internal returns (uint256 deadline) {
        deadline = block.timestamp + 1 days;
        vm.prank(w);
        mgr.postBond(t, hirer, BOND, deadline, expected);
    }

    function _warpPast(SLABondManager m, bytes32 t, address w) internal {
        SLABondManager.Bond memory b = m.getBond(t, w);
        vm.warp(b.deadline + 1);
    }

    // ========================================================================
    // REENTRANCY - token callback re-entering mid-transfer (CEI check)
    // ========================================================================

    function test_Reentrancy_ReleaseBond_TokenCallbackCannotDoublePay() public {
        ReentrantToken rt = new ReentrantToken();
        SLABondManager m = new SLABondManager(address(rt), treasury, address(0), 10_000);
        rt.mint(worker, START);
        vm.prank(worker);
        rt.approve(address(m), type(uint256).max);
        vm.prank(worker);
        m.postBond(taskId, hirer, BOND, block.timestamp + 1 days, bytes32(0));
        vm.prank(worker);
        m.submitSigil(taskId, sigil);

        rt.setTarget(m, taskId, worker);
        rt.arm();
        m.releaseBond(taskId, worker); // token hook fires inside transfer

        assertEq(rt.hookCount(), 1, "hook must actually have fired");
        assertFalse(rt.innerReleaseOk(), "re-entrant release must fail");
        assertFalse(rt.innerSlashOk(), "re-entrant slash must fail");
        assertFalse(rt.innerSubmitOk(), "re-entrant submit must fail");
        assertEq(rt.balanceOf(worker), START, "worker paid exactly once, in full");
        assertEq(rt.balanceOf(address(m)), 0);
    }

    function test_Reentrancy_SlashBond_TokenCallbackCannotDoublePay() public {
        ReentrantToken rt = new ReentrantToken();
        SLABondManager m = new SLABondManager(address(rt), treasury, address(0), 10_000);
        rt.mint(worker, START);
        vm.prank(worker);
        rt.approve(address(m), type(uint256).max);
        vm.prank(worker);
        m.postBond(taskId, hirer, BOND, block.timestamp + 1 days, bytes32(0));
        _warpPast(m, taskId, worker);

        rt.setTarget(m, taskId, worker);
        rt.arm();
        m.slashBond(taskId, worker); // token hook fires inside the hirer transfer

        assertEq(rt.hookCount(), 1, "hook must actually have fired");
        assertFalse(rt.innerReleaseOk(), "re-entrant release must fail");
        assertFalse(rt.innerSlashOk(), "re-entrant slash must fail");
        assertFalse(rt.innerSubmitOk(), "re-entrant submit must fail");
        assertEq(rt.balanceOf(hirer), BOND, "hirer paid exactly once, exactly the bond");
        assertEq(rt.balanceOf(address(m)), 0);
    }

    function test_Reentrancy_PostBond_CallbackCannotExtractOrDuplicate() public {
        ReentrantToken rt = new ReentrantToken();
        SLABondManager m = new SLABondManager(address(rt), treasury, address(0), 10_000);
        rt.mint(worker, START);
        vm.prank(worker);
        rt.approve(address(m), type(uint256).max);
        rt.setTarget(m, taskId, worker);
        rt.arm(); // armed BEFORE the pull: hook fires inside transferFrom

        vm.prank(worker);
        m.postBond(taskId, hirer, BOND, block.timestamp + 1 days, bytes32(0));

        assertEq(rt.hookCount(), 1, "hook must actually have fired");
        assertFalse(rt.innerReleaseOk(), "cannot release a bond mid-post (no SIGIL yet)");
        assertFalse(rt.innerSlashOk(), "cannot slash mid-post (deadline not passed)");
        assertFalse(rt.innerSubmitOk(), "token-keyed submit cannot touch worker's bond");
        SLABondManager.Bond memory b = m.getBond(taskId, worker);
        assertEq(uint256(b.state), uint256(SLABondManager.BondState.Active));
        assertEq(b.amount, BOND);
        assertEq(b.sigilCommitment, bytes32(0), "no SIGIL smuggled in by the callback");
        assertEq(rt.balanceOf(address(m)), BOND, "exactly one pull landed");
    }

    // ========================================================================
    // DOUBLE-PAY / STATE MACHINE EXHAUSTIVENESS
    // ========================================================================

    function test_DoublePay_ReleaseThenSlash_Reverts() public {
        _post(worker, taskId, bytes32(0));
        vm.prank(worker);
        mgr.submitSigil(taskId, sigil);
        mgr.releaseBond(taskId, worker);
        _warpPast(mgr, taskId, worker);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker)
        );
        mgr.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), 0, "hirer got nothing from a released bond");
        assertEq(usdc.balanceOf(worker), START);
    }

    function test_DoublePay_SlashThenRelease_Reverts() public {
        _post(worker, taskId, bytes32(0));
        _warpPast(mgr, taskId, worker);
        mgr.slashBond(taskId, worker);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker)
        );
        mgr.releaseBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START - BOND, "worker never recovers a slashed bond");
        assertEq(usdc.balanceOf(hirer), BOND, "hirer paid exactly once");
    }

    function test_TerminalState_SigilAfterSlashOrRelease_Reverts() public {
        // After slash:
        _post(worker, taskId, bytes32(0));
        _warpPast(mgr, taskId, worker);
        mgr.slashBond(taskId, worker);
        vm.prank(worker);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker)
        );
        mgr.submitSigil(taskId, sigil);
        // After release:
        _post(worker, taskId2, bytes32(0));
        vm.prank(worker);
        mgr.submitSigil(taskId2, sigil);
        mgr.releaseBond(taskId2, worker);
        vm.prank(worker);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId2, worker)
        );
        mgr.submitSigil(taskId2, sigilB);
    }

    // ========================================================================
    // SIGIL GAMES
    // ========================================================================

    /// @notice ATTACK SUCCEEDS - by-design-documented: an unpinned bond is
    ///         rescued by ANY junk non-zero commitment submitted on time.
    ///         This is the contract's stated "commitment existence, not
    ///         correctness" assumption, confirmed as the actual behavior.
    function test_Sigil_JunkUnpinned_ReleasesInFull_BYDESIGN() public {
        _post(worker, taskId, bytes32(0)); // unpinned
        vm.prank(worker);
        mgr.submitSigil(taskId, junkSigil); // 0xDEAD... - no relation to any output
        mgr.releaseBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START, "junk SIGIL rescued the full bond");
        _warpPast(mgr, taskId, worker);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker)
        );
        mgr.slashBond(taskId, worker);
    }

    function test_Sigil_JunkPinned_MismatchReverts_BondStaysSlashable() public {
        _post(worker, taskId, sigil); // pinned
        vm.prank(worker);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.SigilMismatch.selector, sigil, junkSigil)
        );
        mgr.submitSigil(taskId, junkSigil);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.BondNotVerified.selector, taskId, worker)
        );
        mgr.releaseBond(taskId, worker);
        _warpPast(mgr, taskId, worker);
        mgr.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), BOND);
    }

    function test_Sigil_ThirdPartyCannotInjectIntoWorkersBond() public {
        _post(worker, taskId, bytes32(0));
        vm.prank(anyone);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, anyone)
        );
        mgr.submitSigil(taskId, sigil);
        SLABondManager.Bond memory b = mgr.getBond(taskId, worker);
        assertEq(b.sigilCommitment, bytes32(0), "worker's bond untouched by third party");
        // Hirer cannot inject either (also keyed to hirer's own empty bond).
        vm.prank(hirer);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, hirer)
        );
        mgr.submitSigil(taskId, sigil);
    }

    function test_Sigil_CrossWorker_SubmissionDoesNotVerifyOtherBond() public {
        _post(worker, taskId, bytes32(0));
        _post(workerB, taskId, bytes32(0));
        vm.prank(workerB);
        mgr.submitSigil(taskId, sigilB);
        SLABondManager.Bond memory bA = mgr.getBond(taskId, worker);
        assertEq(bA.sigilCommitment, bytes32(0), "B's SIGIL never lands in A's bond record");
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.BondNotVerified.selector, taskId, worker)
        );
        mgr.releaseBond(taskId, worker);
    }

    function test_Sigil_DeadlineMinusOneSecond_IsOnTime() public {
        uint256 deadline = _post(worker, taskId, bytes32(0));
        vm.warp(deadline - 1);
        vm.prank(worker);
        mgr.submitSigil(taskId, sigil);
        assertTrue(mgr.isBondVerified(taskId, worker, sigil));
        mgr.releaseBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START);
    }

    function test_Sigil_DeadlinePlusOneSecond_Reverts() public {
        uint256 deadline = _post(worker, taskId, bytes32(0));
        vm.warp(deadline + 1);
        vm.prank(worker);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.DeadlinePassed.selector, deadline, deadline + 1)
        );
        mgr.submitSigil(taskId, sigil);
    }

    function test_Sigil_ZeroExpectedLookup_NeverReportsVerified() public {
        _post(worker, taskId, bytes32(0));
        assertFalse(mgr.isTaskVerified(taskId, bytes32(0)), "0 == 0 must not count as verified");
        vm.prank(worker);
        mgr.submitSigil(taskId, sigil);
        assertFalse(mgr.isTaskVerified(taskId, bytes32(0)), "recorded non-zero != expected zero");
        assertFalse(mgr.isBondVerified(taskId, worker, bytes32(0)));
    }

    // ========================================================================
    // HUB GRIEFING
    // ========================================================================

    /// @notice With an UNPINNED bond the Hub is never consulted: a task the
    ///         Hub reports verified is still slashed if no internal SIGIL
    ///         exists. Matches the code comment ("when an expectedSigil was
    ///         pinned") - integrator surprise, documented behavior.
    function test_Hub_UnpinnedBond_HubVerifiedDoesNotVetoSlash() public {
        AdvHub hub = new AdvHub();
        SLABondManager m = new SLABondManager(address(usdc), treasury, address(hub), 10_000);
        vm.prank(worker);
        usdc.approve(address(m), type(uint256).max);
        vm.prank(worker);
        m.postBond(taskId, hirer, BOND, block.timestamp + 1 days, bytes32(0)); // unpinned
        hub.setVerified(true); // Hub says the task IS verified
        _warpPast(m, taskId, worker);
        m.slashBond(taskId, worker); // slash proceeds anyway
        assertEq(usdc.balanceOf(hirer), BOND, "hub veto silently skipped for unpinned bonds");
    }

    function test_Hub_RevertingHub_StallsSlash_BondStaysActive() public {
        RevertingHub hub = new RevertingHub();
        SLABondManager m = new SLABondManager(address(usdc), treasury, address(hub), 10_000);
        vm.prank(worker);
        usdc.approve(address(m), type(uint256).max);
        vm.prank(worker);
        m.postBond(taskId, hirer, BOND, block.timestamp + 1 days, sigil); // pinned
        _warpPast(m, taskId, worker);
        vm.expectRevert(); // hub call reverts -> slash reverts (fail-closed stall)
        m.slashBond(taskId, worker);
        SLABondManager.Bond memory b = m.getBond(taskId, worker);
        assertEq(uint256(b.state), uint256(SLABondManager.BondState.Active), "bond intact, slash stalled");
        assertEq(usdc.balanceOf(address(m)), BOND, "funds locked, not lost");
        assertEq(usdc.balanceOf(hirer), 0);
    }

    /// @notice FIXED (F1): two-registry divergence no longer strands a bond
    ///         forever. The stranding preconditions still hold right after
    ///         the deadline (slash vetoed by the Hub, release blocked, late
    ///         submit reverts), AND the escape hatch stays shut for the full
    ///         escape window. Once deadline + HUB_ESCAPE_WINDOW has fully
    ///         passed and the Hub STILL reports the task verified, the bond
    ///         becomes releasable to the worker via escapeHubVerifiedBond.
    ///         (Flipped from
    ///         test_Hub_AlwaysVerifiedHub_PermanentlyStrandsBond_FINDING,
    ///         which proved the bond had no exit at all.)
    function test_Hub_AlwaysVerifiedHub_EscapeAfterWindowReleasesToWorker_FIXED() public {
        AdvHub hub = new AdvHub();
        SLABondManager m = new SLABondManager(address(usdc), treasury, address(hub), 10_000);
        vm.prank(worker);
        usdc.approve(address(m), type(uint256).max);
        uint256 deadline = block.timestamp + 1 days;
        vm.prank(worker);
        m.postBond(taskId, hirer, BOND, deadline, sigil); // pinned
        hub.setVerified(true);
        _warpPast(m, taskId, worker);

        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.BondVerified.selector, taskId, worker)
        );
        m.slashBond(taskId, worker); // vetoed by the Hub
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.BondNotVerified.selector, taskId, worker)
        );
        m.releaseBond(taskId, worker); // no internal SIGIL -> cannot release
        vm.prank(worker);
        vm.expectRevert(); // too late to submit one now
        m.submitSigil(taskId, sigil);

        // The hatch is still shut just past the deadline...
        uint256 window = m.HUB_ESCAPE_WINDOW();
        vm.expectRevert(
            abi.encodeWithSelector(
                SLABondManager.EscapeWindowNotReached.selector, deadline + window, block.timestamp
            )
        );
        m.escapeHubVerifiedBond(taskId, worker);
        // ...and at exactly deadline + window (open iff now > deadline + window).
        vm.warp(deadline + window);
        vm.expectRevert(
            abi.encodeWithSelector(
                SLABondManager.EscapeWindowNotReached.selector, deadline + window, deadline + window
            )
        );
        m.escapeHubVerifiedBond(taskId, worker);
        SLABondManager.Bond memory stuck = m.getBond(taskId, worker);
        assertEq(uint256(stuck.state), uint256(SLABondManager.BondState.Active), "still locked inside the window");

        // One second past the window: permissionless trigger, worker paid in full.
        vm.warp(deadline + window + 1);
        vm.prank(anyone);
        m.escapeHubVerifiedBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START, "escaped bond returned to the worker in full");
        assertEq(usdc.balanceOf(address(m)), 0, "nothing left stranded");
        SLABondManager.Bond memory b = m.getBond(taskId, worker);
        assertEq(uint256(b.state), uint256(SLABondManager.BondState.Escaped), "terminal Escaped state");

        // Terminal: no second exit, no slash, no release after escape.
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker)
        );
        m.escapeHubVerifiedBond(taskId, worker);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker)
        );
        m.slashBond(taskId, worker);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.NoActiveBond.selector, taskId, worker)
        );
        m.releaseBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), 0, "hirer never paid from an escaped (Hub-verified) bond");
    }

    /// @notice F1 guard: a bond the Hub does NOT verify can NEVER escape into
    ///         the worker's hands, no matter how much time passes - it stays
    ///         slashable exactly as before and the slash still pays the hirer.
    function test_HubEscape_NonVerifiedBond_CannotEscape_RemainsSlashable() public {
        AdvHub hub = new AdvHub();
        SLABondManager m = new SLABondManager(address(usdc), treasury, address(hub), 10_000);
        vm.prank(worker);
        usdc.approve(address(m), type(uint256).max);
        uint256 deadline = block.timestamp + 1 days;
        vm.prank(worker);
        m.postBond(taskId, hirer, BOND, deadline, sigil); // pinned
        hub.setVerified(false); // genuine SLA miss: Hub never verified
        vm.warp(deadline + m.HUB_ESCAPE_WINDOW() + 1);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.EscapeNotHubVerified.selector, taskId, worker)
        );
        m.escapeHubVerifiedBond(taskId, worker);
        // Still slashable, in full, after the failed escape attempt.
        m.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), BOND, "slash proceeded exactly as before");
        assertEq(usdc.balanceOf(worker), START - BOND, "worker could not dodge the slash via escape");
    }

    /// @notice F1 guard: the veto (and therefore the escape) only ever applied
    ///         to PINNED bonds. An unpinned bond stays slashable even if the
    ///         Hub reports the task verified, and it can never escape.
    function test_HubEscape_UnpinnedBond_CannotEscape_RemainsSlashable() public {
        AdvHub hub = new AdvHub();
        SLABondManager m = new SLABondManager(address(usdc), treasury, address(hub), 10_000);
        vm.prank(worker);
        usdc.approve(address(m), type(uint256).max);
        uint256 deadline = block.timestamp + 1 days;
        vm.prank(worker);
        m.postBond(taskId, hirer, BOND, deadline, bytes32(0)); // unpinned
        hub.setVerified(true);
        vm.warp(deadline + m.HUB_ESCAPE_WINDOW() + 1);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.EscapeNotHubVerified.selector, taskId, worker)
        );
        m.escapeHubVerifiedBond(taskId, worker);
        m.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), BOND);
    }

    /// @notice F1 guard: with no Hub configured there is nothing to veto a
    ///         slash, so there is nothing to escape - even ~10 years on, a
    ///         missed bond is slash-only.
    function test_HubEscape_NoHubConfigured_CannotEscape() public {
        _post(worker, taskId, bytes32(0)); // default mgr: sigilHub == address(0)
        SLABondManager.Bond memory b0 = mgr.getBond(taskId, worker);
        vm.warp(b0.deadline + mgr.HUB_ESCAPE_WINDOW() + 3650 days);
        vm.expectRevert(
            abi.encodeWithSelector(SLABondManager.EscapeNotHubVerified.selector, taskId, worker)
        );
        mgr.escapeHubVerifiedBond(taskId, worker);
        mgr.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), BOND);
        assertEq(usdc.balanceOf(worker), START - BOND);
    }

    /// @notice The escape payout follows the same CEI discipline as
    ///         release/slash: state flips to Escaped before the transfer, so
    ///         a token callback re-entering release/slash/submit/escape
    ///         mid-payout cannot double-pay.
    function test_Reentrancy_EscapeBond_TokenCallbackCannotDoublePay() public {
        ReentrantToken rt = new ReentrantToken();
        AdvHub hub = new AdvHub();
        SLABondManager m = new SLABondManager(address(rt), treasury, address(hub), 10_000);
        rt.mint(worker, START);
        vm.prank(worker);
        rt.approve(address(m), type(uint256).max);
        uint256 deadline = block.timestamp + 1 days;
        vm.prank(worker);
        m.postBond(taskId, hirer, BOND, deadline, sigil); // pinned
        hub.setVerified(true);
        vm.warp(deadline + m.HUB_ESCAPE_WINDOW() + 1);

        rt.setTarget(m, taskId, worker);
        rt.arm();
        m.escapeHubVerifiedBond(taskId, worker); // token hook fires inside transfer

        assertEq(rt.hookCount(), 1, "hook must actually have fired");
        assertFalse(rt.innerReleaseOk(), "re-entrant release must fail");
        assertFalse(rt.innerSlashOk(), "re-entrant slash must fail");
        assertFalse(rt.innerSubmitOk(), "re-entrant submit must fail");
        assertFalse(rt.innerEscapeOk(), "re-entrant escape must fail");
        assertEq(rt.balanceOf(worker), START, "worker paid exactly once, in full, via escape");
        assertEq(rt.balanceOf(address(m)), 0);
    }

    /// @notice F1 guard: a reverting Hub fails the escape closed, exactly as
    ///         it fails the slash closed - the bond stays Active and intact.
    function test_HubEscape_RevertingHub_FailsClosed_BondStaysActive() public {
        RevertingHub hub = new RevertingHub();
        SLABondManager m = new SLABondManager(address(usdc), treasury, address(hub), 10_000);
        vm.prank(worker);
        usdc.approve(address(m), type(uint256).max);
        uint256 deadline = block.timestamp + 1 days;
        vm.prank(worker);
        m.postBond(taskId, hirer, BOND, deadline, sigil); // pinned
        vm.warp(deadline + m.HUB_ESCAPE_WINDOW() + 1);
        vm.expectRevert(); // hub call reverts -> escape reverts (fail-closed)
        m.escapeHubVerifiedBond(taskId, worker);
        SLABondManager.Bond memory b = m.getBond(taskId, worker);
        assertEq(uint256(b.state), uint256(SLABondManager.BondState.Active), "bond intact, escape stalled");
        assertEq(usdc.balanceOf(address(m)), BOND, "funds locked, not lost");
    }

    function test_Hub_IsImmutable_NoPostDeployChange() public {
        AdvHub hub = new AdvHub();
        SLABondManager m = new SLABondManager(address(usdc), treasury, address(hub), 10_000);
        assertEq(m.sigilHub(), address(hub));
        // Run a full lifecycle; the configured hub never moves. There is no
        // setter/owner function in the contract at all (compile-time fact).
        vm.prank(worker);
        usdc.approve(address(m), type(uint256).max);
        vm.prank(worker);
        m.postBond(taskId, hirer, BOND, block.timestamp + 1 days, sigil);
        vm.prank(worker);
        m.submitSigil(taskId, sigil);
        m.releaseBond(taskId, worker);
        assertEq(m.sigilHub(), address(hub), "hub fixed at construction, forever");
    }

    // ========================================================================
    // postBond GAMES
    // ========================================================================

    /// @notice FIXED (F2): worker naming THEMSELF as hirer now reverts at
    ///         postBond (HirerEqualsWorker). The self-dealing bond can never
    ///         exist, so no slash can ever "pay" the bond back to the worker.
    ///         (Flipped from
    ///         test_PostBond_SelfDealing_HirerEqualsWorker_SlashRefundsWorker_FINDING,
    ///         which proved the slash refunded the worker in full.)
    function test_PostBond_SelfDealing_HirerEqualsWorker_Reverts_FIXED() public {
        vm.prank(worker);
        vm.expectRevert(SLABondManager.HirerEqualsWorker.selector);
        mgr.postBond(taskId, worker, BOND, block.timestamp + 1 days, bytes32(0)); // hirer == worker
        // No funds pulled, no ghost bond left behind.
        assertEq(usdc.balanceOf(worker), START, "no bond pulled for a self-dealing post");
        assertEq(usdc.balanceOf(address(mgr)), 0);
        SLABondManager.Bond memory b = mgr.getBond(taskId, worker);
        assertEq(uint256(b.state), uint256(SLABondManager.BondState.None), "no self-dealing bond recorded");
    }

    function test_PostBond_ZeroTaskIdOrZeroHirer_Reverts() public {
        vm.prank(worker);
        vm.expectRevert(SLABondManager.InvalidConfig.selector);
        mgr.postBond(bytes32(0), hirer, BOND, block.timestamp + 1 days, bytes32(0));
        vm.prank(worker);
        vm.expectRevert(SLABondManager.InvalidConfig.selector);
        mgr.postBond(taskId, address(0), BOND, block.timestamp + 1 days, bytes32(0));
    }

    function test_PostBond_FailedPull_LeavesNoGhostBond() public {
        address broke = address(0x9999);
        usdc.mint(broke, START); // balance but NO approval
        vm.prank(broke);
        vm.expectRevert(SLABondManager.TransferFailed.selector);
        mgr.postBond(taskId, hirer, BOND, block.timestamp + 1 days, bytes32(0));
        SLABondManager.Bond memory b = mgr.getBond(taskId, broke);
        assertEq(uint256(b.state), uint256(SLABondManager.BondState.None), "state write rolled back with the pull");
        assertEq(mgr.taskWorker(taskId), address(0), "index write rolled back too");
        // Once approved, the same (taskId, worker) posts cleanly.
        vm.prank(broke);
        usdc.approve(address(mgr), type(uint256).max);
        vm.prank(broke);
        mgr.postBond(taskId, hirer, BOND, block.timestamp + 1 days, bytes32(0));
        assertEq(usdc.balanceOf(address(mgr)), BOND);
    }

    function test_PostBond_RepostAfterSlash_OverwritesTerminalRecord() public {
        _post(worker, taskId, bytes32(0));
        _warpPast(mgr, taskId, worker);
        mgr.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), BOND, "first bond paid out to hirer");

        // Same (taskId, worker) may bond again: terminal record is replaced.
        uint256 deadline2 = block.timestamp + 1 days;
        vm.prank(worker);
        mgr.postBond(taskId, hirer, BOND, deadline2, sigil);
        SLABondManager.Bond memory b = mgr.getBond(taskId, worker);
        assertEq(uint256(b.state), uint256(SLABondManager.BondState.Active));
        assertEq(b.deadline, deadline2);
        assertEq(b.sigilCommitment, bytes32(0), "old record fully overwritten");
        vm.prank(worker);
        mgr.submitSigil(taskId, sigil);
        mgr.releaseBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), BOND, "hirer kept exactly the first bond - no double-pay");
        assertEq(usdc.balanceOf(address(mgr)), 0);
    }

    /// @notice taskWorker is last-writer-wins: a second worker posting for
    ///         the same taskId makes the FIRST worker's verification
    ///         invisible to the Hub-shaped isTaskVerified(taskId, ...) lookup
    ///         (per-worker release/slash are unaffected). Documented as
    ///         assumption 6 - proven here because external integrators
    ///         reading only the Hub-shaped view WILL be misled.
    function test_TaskWorkerOverwrite_HidesFirstVerificationFromHubLookup() public {
        _post(worker, taskId, bytes32(0));
        vm.prank(worker);
        mgr.submitSigil(taskId, sigil);
        assertTrue(mgr.isTaskVerified(taskId, sigil), "lookup sees worker A before the overwrite");

        _post(workerB, taskId, bytes32(0)); // same taskId, B posts second
        assertEq(mgr.taskWorker(taskId), workerB, "index now points at B");
        assertFalse(mgr.isTaskVerified(taskId, sigil), "A's valid SIGIL vanished from the Hub-shaped view");
        assertTrue(mgr.isBondVerified(taskId, worker, sigil), "per-worker view still correct");

        mgr.releaseBond(taskId, worker); // money path unaffected
        assertEq(usdc.balanceOf(worker), START);
    }

    function test_PostBond_OneSecondDeadline_SlashableImmediatelyAfter() public {
        vm.prank(worker);
        mgr.postBond(taskId, hirer, BOND, block.timestamp + 1, bytes32(0));
        vm.warp(block.timestamp + 2);
        mgr.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), BOND);
    }

    // ========================================================================
    // ACCOUNTING
    // ========================================================================

    /// @notice Fee-on-transfer token (documented assumption 4) confirmed
    ///         broken in practice: the ledger records the full `amount`
    ///         while the contract receives 90%. The first settlement drains
    ///         the pooled collateral cross-subsidised by the other bond;
    ///         the second bond can then never settle - its funds are
    ///         stranded behind a permanently-reverting transfer.
    function test_FeeOnTransferToken_InsolvencyAndStranding_CONFIRMED() public {
        FeeOnTransferToken fot = new FeeOnTransferToken();
        SLABondManager m = new SLABondManager(address(fot), treasury, address(0), 10_000);
        fot.mint(worker, START);
        vm.prank(worker);
        fot.approve(address(m), type(uint256).max);

        vm.prank(worker);
        m.postBond(taskId, hirer, BOND, block.timestamp + 1 days, bytes32(0));
        vm.prank(worker);
        m.postBond(taskId2, hirer, BOND, block.timestamp + 1 days, bytes32(0));
        assertEq(fot.balanceOf(address(m)), (BOND * 180) / 100, "received 90% x2");
        SLABondManager.Bond memory b1 = m.getBond(taskId, worker);
        assertEq(b1.amount, BOND, "ledger still claims the full amount per bond");

        vm.prank(worker);
        m.submitSigil(taskId, sigil);
        _warpPast(m, taskId2, worker);
        // Slash of bond2 pays the FULL ledger amount out of the pooled 90%x2.
        m.slashBond(taskId2, worker);
        assertEq(fot.balanceOf(hirer), BOND, "hirer made whole from the other bond's collateral");
        // Bond1's release now exceeds the remaining balance and reverts forever.
        vm.expectRevert(SLABondManager.TransferFailed.selector);
        m.releaseBond(taskId, worker);
        SLABondManager.Bond memory b1After = m.getBond(taskId, worker);
        assertEq(uint256(b1After.state), uint256(SLABondManager.BondState.Active), "earned bond can never settle");
        assertEq(fot.balanceOf(address(m)), (BOND * 80) / 100, "residue stranded");
    }

    function test_FalseReturnToken_ReleaseRevertsCleanly_ThenSucceedsOnce() public {
        FalseReturnToken frt = new FalseReturnToken();
        SLABondManager m = new SLABondManager(address(frt), treasury, address(0), 10_000);
        frt.mint(worker, START);
        vm.prank(worker);
        frt.approve(address(m), type(uint256).max);
        vm.prank(worker);
        m.postBond(taskId, hirer, BOND, block.timestamp + 1 days, bytes32(0));
        vm.prank(worker);
        m.submitSigil(taskId, sigil);

        frt.setTransfersEnabled(false);
        vm.expectRevert(SLABondManager.TransferFailed.selector);
        m.releaseBond(taskId, worker);
        SLABondManager.Bond memory b = m.getBond(taskId, worker);
        assertEq(uint256(b.state), uint256(SLABondManager.BondState.Active), "state flip rolled back atomically");
        assertEq(b.amount, BOND, "amount intact after failed release");
        assertEq(frt.balanceOf(address(m)), BOND, "no funds leaked on the failed attempt");

        frt.setTransfersEnabled(true);
        m.releaseBond(taskId, worker);
        assertEq(frt.balanceOf(worker), START, "paid exactly once after retry");
    }

    function test_Accounting_ManyBonds_ConservationInvariant() public {
        // Four bonds across three workers on the shared manager.
        _post(worker, taskId, bytes32(0));
        _post(worker, taskId2, bytes32(0));
        _post(workerB, taskId3, bytes32(0));
        _post(workerC, taskId4, bytes32(0));
        assertEq(usdc.balanceOf(address(mgr)), 4 * BOND);

        vm.prank(worker);
        mgr.submitSigil(taskId, sigil);
        vm.prank(workerB);
        mgr.submitSigil(taskId3, sigilB);

        mgr.releaseBond(taskId, worker); // settles bond 1
        assertEq(usdc.balanceOf(address(mgr)), 3 * BOND);

        _warpPast(mgr, taskId2, worker);
        mgr.slashBond(taskId2, worker); // settles bond 2
        mgr.releaseBond(taskId3, workerB); // on-time SIGIL: release works post-deadline

        // Only bond 4 remains, still Active and fully backed.
        assertEq(usdc.balanceOf(address(mgr)), BOND);
        SLABondManager.Bond memory b4 = mgr.getBond(taskId4, workerC);
        assertEq(uint256(b4.state), uint256(SLABondManager.BondState.Active));
        assertEq(b4.amount, BOND, "contract balance == sum of live ledger amounts");
        // Global conservation: nothing created or destroyed.
        uint256 total = usdc.balanceOf(worker) + usdc.balanceOf(workerB) + usdc.balanceOf(workerC)
            + usdc.balanceOf(hirer) + usdc.balanceOf(treasury) + usdc.balanceOf(address(mgr));
        assertEq(total, 3 * START);
    }

    function test_UnclaimedBonds_NoExpiryOnEitherExit() public {
        _post(worker, taskId, bytes32(0));
        _post(worker, taskId2, bytes32(0));
        vm.prank(worker);
        mgr.submitSigil(taskId, sigil);
        vm.warp(block.timestamp + 3650 days); // ~10 years later
        // Neither exit expires: unclaimed funds sit locked until triggered,
        // but they are never permanently stranded (absent the Hub finding).
        mgr.releaseBond(taskId, worker);
        assertEq(usdc.balanceOf(worker), START - BOND);
        mgr.slashBond(taskId2, worker);
        assertEq(usdc.balanceOf(hirer), BOND);
        assertEq(usdc.balanceOf(address(mgr)), 0);
    }

    function test_SlashSplit_Rounding_RemainderAbsorbsDust() public {
        SLABondManager m = new SLABondManager(address(usdc), treasury, address(0), 8_000);
        vm.prank(worker);
        usdc.approve(address(m), type(uint256).max);
        vm.prank(worker);
        m.postBond(taskId, hirer, 101, block.timestamp + 1 days, bytes32(0)); // odd amount
        _warpPast(m, taskId, worker);
        m.slashBond(taskId, worker);
        assertEq(usdc.balanceOf(hirer), 80, "floor(101 * 8000 / 10000) = 80");
        assertEq(usdc.balanceOf(treasury), 21, "treasury absorbs the rounding dust");
        assertEq(usdc.balanceOf(address(m)), 0, "split pays out the full 101 - nothing stranded");
    }
}
