// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title ISigilHubView
 * @notice Minimal view of the SincorSettlementHub SIGIL registry, copied
 *         verbatim in shape from the spec-drop Hub draft
 *         (spec-drop-2026-11-11/SincorSettlementHub.sol) and from the
 *         ERC-7579 SIGIL module lane's ISincorSettlementHub. The Hub
 *         records a SIGIL commitment per taskId at settlement;
 *         `isTaskVerified(taskId, expectedSigil)` is true iff a non-zero
 *         commitment was recorded for the task AND equals `expectedSigil`.
 */
interface ISigilHubView {
    function isTaskVerified(bytes32 taskId, bytes32 expectedSigil) external view returns (bool);
}

/**
 * @title SLABondManager
 * @notice Workload Declaration 2, Lane 5: Pre-Execution SLA Staking &
 *         Slashing. A worker's bid carries a micro-bond (ERC-20, USDC in
 *         production / mock USDC in tests) locked at bid/accept time and
 *         bound to (taskId, worker, deadline), where `deadline` is the
 *         deadline from the SEAL envelope for that task.
 *
 *         Outcomes:
 *         - Worker records a valid SIGIL commitment on or before the
 *           deadline -> bond returns IN FULL to the worker at settlement
 *           via `releaseBond` (permissionless trigger, fixed payee).
 *         - Deadline passes with NO valid SIGIL -> bond auto-slashes to
 *           the hiring agent as latency compensation via `slashBond`
 *           (permissionless trigger). The no-valid-SIGIL condition is
 *           CHECKED on-chain at slash time (internal registry + optional
 *           external Hub), never assumed from the clock alone.
 *
 * @dev Reused machinery / patterns (no parallel universe):
 *      - StakeSlashManager.sol: single-purpose bonding contract shape —
 *        immutable config in the constructor with zero-address guards,
 *        custom errors, Checks-Effects-Interactions, and low-level
 *        `_safeTransfer` / `_safeTransferFrom` ERC-20 calls (the SUT token
 *        surface is transfer/transferFrom only, exactly as in
 *        StakeSlashManager). Its adjudicator-signed slash is NOT reused:
 *        an SLA miss is a clock+registry fact, not a judgment call, so
 *        this slash is permissionless and condition-checked instead.
 *      - ExecutionEscrowManager.sol: the permissionless `timeout()`
 *        liveness pattern — anyone may fire the deadline transition, the
 *        contract checks the state + clock itself, state is finalized
 *        BEFORE any payout, and "you cannot slash what was never staked"
 *        (a missing bond reverts rather than paying out). Its ghosting
 *        branch (ExecutionPhase + deadline passed + no result -> slash)
 *        is the direct ancestor of `slashBond`.
 *      - SincorSettlementHub draft (spec-drop-2026-11-11): the
 *        `isTaskVerified(taskId, expectedSigil)` pattern —
 *        `verifiedTaskSigils[taskId]` equality against a recorded non-zero
 *        commitment — mirrored here per (taskId, worker), plus the Hub's
 *        deadline boundary: a seal is expired iff
 *        `block.timestamp > deadline`, so a SIGIL at exactly `deadline`
 *        is still on time.
 *      - SincorTaskEscrow.sol: the task-bound bond lifecycle
 *        (bond pulled at claim, returned on Verified, forfeited on
 *        Slashed) — generalized here from its 50/50 reporter/treasury
 *        split to the declaration's 100%-to-hirer default.
 *
 *      Fee canon: the platform fee canon is 5% (500 bps), locked, and is
 *      NOT touched here. Bond and slash amounts are not fees: a released
 *      bond returns 100% and a slashed bond pays out per
 *      `slashToHirerBps` (default 10_000 = 100% to the hirer, remainder —
 *      only if a deployment deliberately sets it lower — to
 *      `slashRemainderTreasury`). No fee is skimmed anywhere in this
 *      contract.
 *
 *      Deadline / grace: NONE by default. There is no grace parameter.
 *      A SIGIL submitted after the deadline reverts and cannot rescue the
 *      bond ("deadline is deadline"). Adding grace is a founder decision.
 *
 *      Trust assumptions (for adversarial review — read precisely):
 *      1. SIGIL "validity" on-chain means exactly: a non-zero commitment,
 *         recorded by the bonded worker for this (taskId, worker), at
 *         `block.timestamp <= deadline`, and — when the bond was posted
 *         with a non-zero `expectedSigil` — equal to that expected value.
 *         Like the Hub itself (see hub_engine.py's trust model), this
 *         contract CANNOT verify that the commitment matches the real
 *         work output; output correctness is enforced off-chain by the
 *         facilitator/verifier before settlement. A worker who records
 *         a junk non-zero commitment on time will get the bond back
 *         unless `expectedSigil` pinning is used.
 *      2. `deadline`, `hirer` and `expectedSigil` are taken as given at
 *         `postBond` time. The poster of the bond is the worker locking
 *         their OWN funds, so a wrong deadline/amount only harms the
 *         worker; a wrong `hirer`, however, would divert slash
 *         compensation. The hirer MUST verify the on-chain bond record
 *         (see `getBond`) against the SEAL envelope before accepting the
 *         bid — acceptance is the hirer's check, exactly as the Hub's
 *         SEAL signature is verified by its counterparty fields.
 *         F2 fix: the one `hirer` error that voids the bond's deterrent
 *         entirely — `hirer == worker` (self-dealing) — is no longer left
 *         to that off-chain check: `postBond` reverts `HirerEqualsWorker`,
 *         so a self-dealing bond can never exist on-chain.
 *      3. The optional external Hub check in `slashBond` is fail-closed
 *         in one direction only: if a Hub is configured AND an
 *         `expectedSigil` was pinned AND `hub.isTaskVerified` returns
 *         true, the slash is blocked. The Hub records no timestamp, so a
 *         Hub record alone never releases a bond via `releaseBond` (it
 *         cannot prove the commitment was on time) — on the normal path
 *         it can only veto a slash. If the Hub call reverts, `slashBond`
 *         reverts (no slash on an unchecked condition); a malfunctioning
 *         Hub can stall slashes but can never cause a wrongful one.
 *         F1 fix: that veto previously stranded such a bond forever (slash
 *         vetoed, release blocked, late submit impossible). The single
 *         exception is now the timed escape hatch,
 *         `escapeHubVerifiedBond`: strictly after
 *         `deadline + HUB_ESCAPE_WINDOW` (30 days), a bond the Hub STILL
 *         reports verified under the same predicate may return to the
 *         worker. The delay is safe precisely because the veto means no
 *         legitimate slash existed during the window; a bond the Hub does
 *         not verify can never escape, at any time, and stays slashable.
 *      4. Token assumption: a standard ERC-20 with transfer/transferFrom
 *         semantics like StakeSlashManager's collateral (no fee-on-
 *         transfer, no rebasing). A fee-on-transfer token would leave the
 *         contract under-collateralized versus its ledger; USDC on Base
 *         satisfies this assumption.
 *      5. `releaseBond` / `slashBond` are permissionless with FIXED
 *         payees taken from the bond record (worker / hirer+treasury).
 *         The caller can never redirect funds, only trigger a transition
 *         whose conditions the contract checks itself.
 */
contract SLABondManager {
    // --- types ------------------------------------------------------------
    enum BondState {
        None,
        Active,
        Released,
        Slashed,
        Escaped // terminal: Hub-vetoed bond returned to the worker via the timed escape hatch (F1 fix)
    }

    struct Bond {
        bytes32 taskId;
        address worker;
        address hirer;
        uint256 amount;
        uint256 deadline; // SEAL envelope deadline (unix seconds)
        bytes32 expectedSigil; // 0 = unpinned: any on-time non-zero SIGIL counts
        bytes32 sigilCommitment; // recorded SIGIL; 0 until submitted
        uint256 sigilSubmittedAt; // 0 until submitted
        BondState state;
    }

    // --- errors -----------------------------------------------------------
    error InvalidConfig();
    error InvalidAmount();
    error InvalidDeadline();
    error Unauthorized();
    error BondAlreadyExists(bytes32 taskId, address worker);
    error NoActiveBond(bytes32 taskId, address worker);
    error SigilZero();
    error SigilMismatch(bytes32 expected, bytes32 got);
    error SigilAlreadySubmitted(bytes32 taskId, address worker);
    error DeadlinePassed(uint256 deadline, uint256 now_);
    error DeadlineNotPassed(uint256 deadline, uint256 now_);
    error BondVerified(bytes32 taskId, address worker);
    error BondNotVerified(bytes32 taskId, address worker);
    error HirerEqualsWorker();
    error EscapeWindowNotReached(uint256 availableAt, uint256 now_);
    error EscapeNotHubVerified(bytes32 taskId, address worker);
    error TransferFailed();

    // --- events -----------------------------------------------------------
    event BondPosted(
        bytes32 indexed taskId,
        address indexed worker,
        address indexed hirer,
        uint256 amount,
        uint256 deadline,
        bytes32 expectedSigil
    );
    event SigilSubmitted(bytes32 indexed taskId, address indexed worker, bytes32 sigilCommitment, uint256 submittedAt);
    event BondReleased(bytes32 indexed taskId, address indexed worker, uint256 amount);
    event BondEscaped(bytes32 indexed taskId, address indexed worker, uint256 amount);
    event BondSlashed(
        bytes32 indexed taskId,
        address indexed worker,
        address indexed hirer,
        uint256 toHirer,
        uint256 toTreasury
    );

    // --- storage ----------------------------------------------------------
    /// @notice Bond token (USDC in production; mock USDC in tests).
    address public immutable bondToken;
    /// @notice Receives the slash remainder ONLY when slashToHirerBps < 10_000.
    address public immutable slashRemainderTreasury;
    /// @notice Optional SincorSettlementHub (zero = internal registry only).
    address public immutable sigilHub;
    /// @notice Share of a slashed bond paid to the hirer, in bps.
    ///         Declaration default: 10_000 (100% to hirer).
    uint256 public immutable slashToHirerBps;

    /// @notice F1 fix: delay after a bond's deadline before a Hub-vetoed
    ///         bond may escape back to the worker (see
    ///         `escapeHubVerifiedBond`). 30 days is deliberately LONG
    ///         relative to SLA settlement cycles (the test fixture deadline
    ///         is 1 day): while the Hub reports the task verified, the slash
    ///         is vetoed for the entire window, so no legitimate slash is
    ///         lost by waiting — the window only delays the worker's
    ///         recovery, and it gives the hirer/facilitator a full month to
    ///         correct a wrong Hub record (after a correction the escape
    ///         check fails and the bond is slashable again) before any
    ///         funds can move.
    uint256 public constant HUB_ESCAPE_WINDOW = 30 days;

    /// @dev Bonds keyed by (taskId, worker): keccak256(abi.encode(taskId, worker)).
    mapping(bytes32 => Bond) public bonds;
    /// @dev taskId -> the worker whose bond backs it (single-winner tasks),
    ///      enabling the Hub-shaped isTaskVerified(taskId, sigil) lookup.
    mapping(bytes32 => address) public taskWorker;

    constructor(
        address _bondToken,
        address _slashRemainderTreasury,
        address _sigilHub,
        uint256 _slashToHirerBps
    ) {
        if (_bondToken == address(0)) revert InvalidConfig();
        if (_slashToHirerBps > 10_000) revert InvalidConfig();
        // A remainder can only exist when the hirer share is below 100%;
        // it must have somewhere to go.
        if (_slashToHirerBps < 10_000 && _slashRemainderTreasury == address(0)) {
            revert InvalidConfig();
        }
        bondToken = _bondToken;
        slashRemainderTreasury = _slashRemainderTreasury;
        sigilHub = _sigilHub; // zero allowed: internal registry only
        slashToHirerBps = _slashToHirerBps;
    }

    // --- helpers ------------------------------------------------------------
    function bondKey(bytes32 taskId, address worker) public pure returns (bytes32) {
        return keccak256(abi.encode(taskId, worker));
    }

    function getBond(bytes32 taskId, address worker) external view returns (Bond memory) {
        return bonds[bondKey(taskId, worker)];
    }

    /**
     * @notice Hub-pattern verification check, per (taskId, worker): true
     *         iff a non-zero SIGIL was recorded for this bond on or before
     *         its deadline and equals `expectedSigil`. Mirrors the Hub's
     *         `recorded != 0 && recorded == expectedSigil`, plus the
     *         on-time condition the Hub's settlement-time recording made
     *         implicit (a late submission can never be recorded here, so
     *         `sigilSubmittedAt <= deadline` is defense-in-depth).
     */
    function isBondVerified(bytes32 taskId, address worker, bytes32 expectedSigil) public view returns (bool) {
        Bond storage b = bonds[bondKey(taskId, worker)];
        return _isVerified(b, expectedSigil);
    }

    /**
     * @notice Hub-shaped lookup: isTaskVerified(taskId, expectedSigil) over
     *         the worker bonded to this taskId. Same semantics as the
     *         Hub's, so existing Hub-pattern callers can read either
     *         contract the same way.
     */
    function isTaskVerified(bytes32 taskId, bytes32 expectedSigil) external view returns (bool) {
        address worker = taskWorker[taskId];
        if (worker == address(0)) return false;
        return isBondVerified(taskId, worker, expectedSigil);
    }

    function _isVerified(Bond storage b, bytes32 expectedSigil) internal view returns (bool) {
        return b.state == BondState.Active
            && b.sigilCommitment != bytes32(0)
            && b.sigilSubmittedAt <= b.deadline
            && b.sigilCommitment == expectedSigil;
    }

    /// @dev The external-Hub veto condition, shared verbatim by slashBond
    ///      and the escape hatch so the two can never drift apart: true iff
    ///      a Hub is configured AND an expectedSigil was pinned AND the Hub
    ///      reports the task verified. A reverting Hub propagates the
    ///      revert (fail-closed) in both callers.
    function _isHubVerified(Bond storage b) internal view returns (bool) {
        if (sigilHub == address(0) || b.expectedSigil == bytes32(0)) return false;
        return ISigilHubView(sigilHub).isTaskVerified(b.taskId, b.expectedSigil);
    }

    /// @dev Verified against the bond's OWN recorded commitment (and its
    ///      pinned expectation, when one was set at post time).
    function _hasValidSigil(Bond storage b) internal view returns (bool) {
        if (b.sigilCommitment == bytes32(0)) return false;
        if (b.sigilSubmittedAt > b.deadline) return false;
        if (b.expectedSigil != bytes32(0) && b.sigilCommitment != b.expectedSigil) return false;
        return true;
    }

    // --- bonding ------------------------------------------------------------
    /**
     * @notice Lock the worker's micro-bond for a bid. Called by the worker
     *         (msg.sender) at bid/accept time; pulls `amount` of bondToken
     *         via transferFrom (worker must approve first — same flow as
     *         StakeSlashManager.stake).
     * @param taskId        SEAL envelope taskId.
     * @param hirer         Hiring agent from the SEAL envelope (slash payee).
     * @param amount        Bond amount in bondToken units (e.g. USDC 6dp).
     * @param deadline      SEAL envelope deadline (unix seconds), future.
     * @param expectedSigil Optional pinned SIGIL commitment; bytes32(0)
     *                      leaves the bond unpinned (output not yet known
     *                      at bid time — the common case).
     */
    function postBond(
        bytes32 taskId,
        address hirer,
        uint256 amount,
        uint256 deadline,
        bytes32 expectedSigil
    ) external {
        if (hirer == address(0) || taskId == bytes32(0)) revert InvalidConfig();
        // F2 fix: a self-dealing bond (hirer == worker) can never deter
        // anything — its slash would pay the bond straight back to the
        // worker — so it is rejected in-contract at creation instead of
        // relying on off-chain getBond vetting by the hirer.
        if (hirer == msg.sender) revert HirerEqualsWorker();
        if (amount == 0) revert InvalidAmount();
        if (deadline <= block.timestamp) revert InvalidDeadline();

        bytes32 key = bondKey(taskId, msg.sender);
        if (bonds[key].state == BondState.Active) revert BondAlreadyExists(taskId, msg.sender);

        // Effects before interactions.
        bonds[key] = Bond({
            taskId: taskId,
            worker: msg.sender,
            hirer: hirer,
            amount: amount,
            deadline: deadline,
            expectedSigil: expectedSigil,
            sigilCommitment: bytes32(0),
            sigilSubmittedAt: 0,
            state: BondState.Active
        });
        taskWorker[taskId] = msg.sender;

        emit BondPosted(taskId, msg.sender, hirer, amount, deadline, expectedSigil);
        _safeTransferFrom(msg.sender, address(this), amount);
    }

    // --- SIGIL ----------------------------------------------------------------
    /**
     * @notice Record the worker's SIGIL commitment for their bond. Only
     *         the bonded worker, only while the bond is Active, and only
     *         on or before the deadline (Hub boundary: expired iff
     *         now > deadline). A commitment is binding: it cannot be
     *         replaced once submitted. A late submission reverts — there
     *         is no grace period — and can never rescue the bond.
     */
    function submitSigil(bytes32 taskId, bytes32 sigilCommitment) external {
        Bond storage b = bonds[bondKey(taskId, msg.sender)];
        if (b.state != BondState.Active) revert NoActiveBond(taskId, msg.sender);
        if (sigilCommitment == bytes32(0)) revert SigilZero();
        if (b.sigilCommitment != bytes32(0)) revert SigilAlreadySubmitted(taskId, msg.sender);
        if (block.timestamp > b.deadline) revert DeadlinePassed(b.deadline, block.timestamp);
        if (b.expectedSigil != bytes32(0) && sigilCommitment != b.expectedSigil) {
            revert SigilMismatch(b.expectedSigil, sigilCommitment);
        }
        b.sigilCommitment = sigilCommitment;
        b.sigilSubmittedAt = block.timestamp;
        emit SigilSubmitted(taskId, msg.sender, sigilCommitment, block.timestamp);
    }

    // --- settlement: bond back --------------------------------------------------
    /**
     * @notice Return the bond IN FULL to the worker at settlement.
     *         Permissionless trigger; payee is always the bonded worker.
     *         Requires a valid on-time SIGIL (checked, not assumed).
     *         State flips to Released BEFORE the transfer, so a second
     *         claim reverts NoActiveBond — double-claim is impossible.
     */
    function releaseBond(bytes32 taskId, address worker) external {
        Bond storage b = bonds[bondKey(taskId, worker)];
        if (b.state != BondState.Active) revert NoActiveBond(taskId, worker);
        if (!_hasValidSigil(b)) revert BondNotVerified(taskId, worker);

        uint256 amount = b.amount;
        b.state = BondState.Released;
        b.amount = 0;
        emit BondReleased(taskId, worker, amount);
        _safeTransfer(worker, amount);
    }

    // --- settlement: slash -------------------------------------------------------
    /**
     * @notice Slash a missed-SLA bond to the hirer as latency compensation.
     *         Permissionless trigger; payees are fixed by the bond record.
     *         ALL conditions are checked on-chain:
     *           1. bond exists and is Active (double-slash reverts),
     *           2. deadline has passed (now > deadline; early slash reverts),
     *           3. NO valid SIGIL: internal registry shows no on-time
     *              commitment, AND — when a Hub is configured and an
     *              expectedSigil was pinned — the Hub does not report the
     *              task verified either. A valid SIGIL in either registry
     *              blocks the slash (BondVerified).
     *         Split: slashToHirerBps of the bond to the hirer (default
     *         100%), any remainder to slashRemainderTreasury.
     */
    function slashBond(bytes32 taskId, address worker) external {
        Bond storage b = bonds[bondKey(taskId, worker)];
        if (b.state != BondState.Active) revert NoActiveBond(taskId, worker);
        if (block.timestamp <= b.deadline) revert DeadlineNotPassed(b.deadline, block.timestamp);
        if (_hasValidSigil(b)) revert BondVerified(taskId, worker);
        if (_isHubVerified(b)) revert BondVerified(taskId, worker);

        uint256 amount = b.amount;
        address hirer = b.hirer;
        uint256 toHirer = (amount * slashToHirerBps) / 10_000;
        uint256 toTreasury = amount - toHirer;

        // Effects before interactions.
        b.state = BondState.Slashed;
        b.amount = 0;
        emit BondSlashed(taskId, worker, hirer, toHirer, toTreasury);

        if (toHirer > 0) _safeTransfer(hirer, toHirer);
        if (toTreasury > 0) _safeTransfer(slashRemainderTreasury, toTreasury);
    }

    // --- settlement: timed escape for Hub-vetoed bonds ---------------------------
    /**
     * @notice F1 fix: timed escape hatch for a Hub-vetoed bond that would
     *         otherwise be stranded forever. The stranded state is exact:
     *         the Hub recorded the task's SIGIL (so `slashBond` is vetoed,
     *         `BondVerified`) but no internal `submitSigil` ever landed (so
     *         `releaseBond` reverts `BondNotVerified`, and after the
     *         deadline a late submit reverts too). This hatch is the ONLY
     *         exit added, and it is permissionless with a fixed payee (the
     *         worker), like the other settlement triggers.
     *
     *         Why it cannot dodge a legitimate slash — ALL must hold at
     *         escape time, checked in this order:
     *           1. bond is Active;
     *           2. `now > deadline + HUB_ESCAPE_WINDOW` (30 days): the full
     *              slash opportunity has run — and kept running — for a
     *              month past the deadline before the hatch even opens
     *              (boundary mirrors the deadline rule: at exactly
     *              deadline + window the hatch is still shut);
     *           3. the bond is Hub-verified RIGHT NOW under the exact veto
     *              predicate `_isHubVerified` (Hub configured, expectedSigil
     *              pinned, Hub reports verified). A bond whose task was NOT
     *              Hub-verified fails here at any time — even years later —
     *              and remains slashable exactly as before; if a Hub record
     *              is corrected during the window, the escape fails and the
     *              slash opens instead. While the Hub reports verified,
     *              the slash is vetoed anyway, so the hatch releases only
     *              bonds no legitimate slash could ever have taken.
     *         A reverting Hub reverts the escape (fail-closed, same as the
     *         slash). State flips to Escaped and amount zeroes BEFORE the
     *         transfer (CEI), so escape/release/slash can never double-pay.
     */
    function escapeHubVerifiedBond(bytes32 taskId, address worker) external {
        Bond storage b = bonds[bondKey(taskId, worker)];
        if (b.state != BondState.Active) revert NoActiveBond(taskId, worker);
        uint256 availableAt = b.deadline + HUB_ESCAPE_WINDOW;
        if (block.timestamp <= availableAt) revert EscapeWindowNotReached(availableAt, block.timestamp);
        if (!_isHubVerified(b)) revert EscapeNotHubVerified(taskId, worker);

        uint256 amount = b.amount;
        b.state = BondState.Escaped;
        b.amount = 0;
        emit BondEscaped(taskId, worker, amount);
        _safeTransfer(worker, amount);
    }

    // --- internals (mirrored from StakeSlashManager) -----------------------------
    function _safeTransfer(address to, uint256 amount) internal {
        (bool ok, bytes memory ret) = bondToken.call(
            abi.encodeWithSignature("transfer(address,uint256)", to, amount)
        );
        if (!ok || (ret.length > 0 && !abi.decode(ret, (bool)))) revert TransferFailed();
    }

    function _safeTransferFrom(address from, address to, uint256 amount) internal {
        (bool ok, bytes memory ret) = bondToken.call(
            abi.encodeWithSignature("transferFrom(address,address,uint256)", from, to, amount)
        );
        if (!ok || (ret.length > 0 && !abi.decode(ret, (bool)))) revert TransferFailed();
    }
}
