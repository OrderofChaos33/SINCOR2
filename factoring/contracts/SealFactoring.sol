// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title SealFactoring — Workload Declaration 2, Lane 4: Ephemeral Micro-Swarm Factoring
 * @notice DESIGN + LOCAL-CHAIN PROTOTYPE ONLY. Not deployed. No mainnet, no real funds.
 *
 * @dev CORE COLLATERAL RULE (the whole point of this lane):
 *      A SEAL is an EIP-712 `SealAuthorization` under domain
 *      {name: "SINCOR_SEAL", version: "1", chainId, verifyingContract} —
 *      the exact shape used by `sincor_x402.hub_engine` (see
 *      ~/workspace/sincor-x402/src/sincor_x402/hub_engine.py). A SEAL is a
 *      SIGNATURE. Per the hub engine's own trust model, "the SEAL signature
 *      alone moves nothing, and nothing is escrowed at authorization time."
 *
 *      Therefore: **an unlocked signature alone is NOT collateral.**
 *      A receivable becomes factorable collateral ONLY when the buyer's
 *      funds are verifiably LOCKED in {SealEscrowVault} for that taskId —
 *      i.e. the vault's token balance verifiably increased via
 *      `lockEscrow()`. {SealFactoring.issueAdvance} reverts with
 *      `EscrowNotLocked` for any seal that is merely registered/signed but
 *      unfunded, and this is covered by an explicit negative test.
 *      FIX (finding 10): `lockEscrow` now measures the ACTUAL balance delta
 *      (after − before) and accounts the receivable against the RECEIVED
 *      amount (`lockedAmountOf`), so a fee-on-transfer token can neither
 *      overstate the collateral nor wedge the exits: release/refund move
 *      exactly the received amount, never the nominal seal amount.
 *
 *      MONEY PATH:
 *        1. Buyer signs SEAL -> `vault.registerSeal(...)` (signature verified
 *           on-chain, nonce consumed; NO funds move).
 *        2. Buyer funds escrow -> `vault.lockEscrow(taskId)` pulls `amount`
 *           via transferFrom into the vault. Receivable is now LOCKED for
 *           the amount actually received.
 *        3. Factor issues an advance -> `factoring.issueAdvance(taskId, amt)`
 *           creates a SPEND CREDIT for the worker (provider). No free cash:
 *           the credit can only leave through `spendAdvance`. FIXES:
 *           buyer == provider seals are rejected (`SelfDealingBlocked`);
 *           a worker (or buyer) with outstanding `clawbackDebtOf` cannot
 *           draw a new advance (`OutstandingClawbackDebt`); a minimum
 *           advance (`MIN_ADVANCE`) blocks dust-advance taskId squatting;
 *           the factoring fee and the payee-allowlist epoch are SNAPSHOTTED
 *           at issuance (later admin edits never touch an in-flight
 *           advance).
 *        4. Scoped spend -> `factoring.spendAdvance(taskId, payee, amt)`
 *           pays ONLY payees that were allowlisted AT ISSUANCE (snapshot
 *           semantics). A payee allowlisted only later, or removed later,
 *           is judged by the issuance-time snapshot.
 *        5a. SIGIL settlement -> `factoring.settle(taskId, sigilCommitment)`:
 *           FIX (finding 1, role separation): settlement requires TWO
 *           distinct roles to have acted — the `verifier` must first record
 *           an on-chain attestation (`attestVerification`) binding this
 *           taskId to the exact commitment, and only then may the `settler`
 *           execute `settle` with that same commitment. The vault releases
 *           the locked gross to the factoring contract, which deducts, IN
 *           ORDER: (i) the 5% platform fee (500 bps, canon — untouched;
 *           FIX finding 9: computed with CEIL division so it never silently
 *           truncates to 0 on tiny seals), (ii) the amount actually
 *           financed (spent advance) + the factoring fee AT THE SNAPSHOTTED
 *           issuance rate, retained by the factor, and (iii) pays the
 *           REMAINDER to the worker. Unspent credit is cancelled.
 *        5b. Failure / expiry -> buyer-initiated failure is a DISPUTE, not a
 *           unilateral execution (FIXES findings 1/6): `markFailed` by the
 *           buyer only OPENS a dispute window ({DISPUTE_WINDOW}) during
 *           which the settler/verifier may still settle (contest). The
 *           settler may execute failure only to adjudicate an open dispute,
 *           or after the seal deadline; after the dispute window lapses
 *           uncontested, anyone may `finalizeFailure`. Permissionless
 *           `expire(taskId)` remains the after-deadline path. On failure the
 *           vault refunds the locked amount to the BUYER, unspent credit is
 *           cancelled, the worker's bond is seized up to the amount owed
 *           (spent + snapshotted factoring fee), and any residual is booked
 *           as `clawbackDebtOf[worker]`.
 *
 *      DEADLINE RULE (FIX finding 7 — one coherent rule, no race):
 *        The seal deadline is the LAST VALID SECOND for issue/spend/settle:
 *        those actions require `block.timestamp <= deadline`; `expire`
 *        requires `block.timestamp > deadline`. The two sets never overlap,
 *        so after the deadline settlement is deterministically blocked
 *        (`SealExpired`) and `expire` cannot front-run a settlement that was
 *        includable before the deadline.
 *
 *      VAULT MODULE REPOINTING (FIX finding 2): the FIRST
 *      `setSettlementModule` (initial wiring, module == address(0)) takes
 *      effect immediately. Every LATER change is two-step and timelocked:
 *      `setSettlementModule` only PROPOSES (effective after
 *      {MODULE_CHANGE_DELAY}); `executeSettlementModuleChange` applies it —
 *      and reverts while ANY escrow is still locked
 *      (`ModuleChangeBlockedByActiveEscrow`), a superset of "while any
 *      advance is active", since every active advance sits on locked escrow.
 *      ROUND-2 FIX (dust-lock interaction of fixes (b)/(h)): a locked seal
 *      that can NEVER back an advance (gross so small its max advance is
 *      below {SealFactoring.MIN_ADVANCE}) previously pinned that count
 *      forever — every exit required an Active advance — stranding the
 *      buyer's funds and DoSing the repoint. `reclaimLockedEscrow` is the
 *      missing no-advance exit: the buyer alone may reclaim a locked
 *      escrow in full (received amount) while NO advance exists for the
 *      task, decrementing the count exactly once. The repoint block is
 *      unchanged for escrow genuinely locked in service of an advance.
 *
 *      ROLE SEPARATION AT CONSTRUCTION (ROUND-2 FIX): round 1 enforced
 *      `RolesMustBeSeparate` only in the settler/verifier setters, while
 *      the constructor set `verifier = admin` without checking the
 *      constructor-supplied settler — deploying with settler == admin
 *      collapsed attest + settle onto one key. The constructor now
 *      reverts `RolesMustBeSeparate` on that collision; the constructor
 *      and the two setters are the only assignments of either role, so
 *      the invariant now holds on every initialization path.
 *
 *      RESIDUAL TRUST ASSUMPTIONS — stated plainly, post-fix:
 *        - SIGIL verification CORRECTNESS is still off-chain. The on-chain
 *          guarantee is now "a verifier independent of the settler attested
 *          this exact commitment for this task, and the settler executed
 *          it" — NOT "the output was verified". A settler ALONE can no
 *          longer settle unverified work or unilaterally fail a healthy
 *          pre-deadline advance; but a COLLUDING (or commonly compromised)
 *          verifier + settler pair can still settle unverified work, and
 *          the verifier alone is trusted to attest honestly. This two-key
 *          requirement is the strongest guard available without redesigning
 *          the documented Hub trust model (verification lives off-chain);
 *          production must custody the two keys independently.
 *        - The factor `admin` still controls the payee allowlist and the
 *          factoring fee — but ONLY for advances issued AFTER the change
 *          (issuance snapshots). Admin cannot redirect the worker remainder
 *          or the platform fee, and cannot release escrow.
 *        - The vault `admin` can still repoint the settlement module when
 *          NO escrow is locked (e.g. before wiring real funds) — that is
 *          the intended wiring operation, now timelocked whenever a module
 *          already exists.
 *        - On default, recovery is still limited to the worker's bond; the
 *          new control is preventive: booked clawback debt now GATES all
 *          future advances for that worker/buyer, so the self-dealing
 *          recycle loop is closed (buyer == provider is rejected outright,
 *          and a defaulter cannot draw again until the ledger is cleared
 *          by a future collection mechanism — none exists in this prototype).
 *        - Token is still assumed non-rebasing; fee-on-transfer tokens are
 *          now ACCOUNTED correctly at lock time (received-delta), though
 *          downstream transfers of such a token still deliver less than the
 *          nominal waterfall amounts to recipients (the token's own fee).
 *
 *      PRICING — FOUNDER DECISION REQUIRED:
 *        - Platform fee: 500 bps (5%) — LOCKED CANON, untouched (only its
 *          rounding is fixed: ceil division, minimum effect 1 wei on any
 *          gross > 0).
 *        - Factoring fee: `factoringFeeBps` is a constructor parameter.
 *          20 bps (0.2%) is a NEW PROPOSAL with no canon behind it; the
 *          tests instantiate 20 bps to exercise the proposed number, but
 *          the value is a FOUNDER PRICING DECISION and must not be treated
 *          as ratified. Hard-capped at MAX_FACTORING_FEE_BPS regardless.
 */

/// @notice The SEAL receivable shape — field-for-field the SealAuthorization
///         struct from the SincorSettlementHub / hub_engine.py.
struct SealAuthorization {
    bytes32 taskId;
    address buyer;
    address provider; // the worker who will be paid at settlement
    uint256 amount; // gross receivable, in token atomic units
    bytes32 expectedSchemaHash;
    uint256 deadline; // unix seconds; seal void after this
    uint256 nonce; // per-buyer, single-use (mirrors Hub `nonces(buyer)`)
}

interface ISealEscrowVault {
    function getReceivable(bytes32 taskId)
        external
        view
        returns (SealAuthorization memory seal, bool locked, bool settled, bool failed);
    function isLocked(bytes32 taskId) external view returns (bool);
    function lockedAmountOf(bytes32 taskId) external view returns (uint256);
    function token() external view returns (IERC20);
    function releaseForSettlement(bytes32 taskId, address to) external returns (uint256 amount);
    function refundOnFailure(bytes32 taskId) external returns (uint256 amount);
    function reclaimLockedEscrow(bytes32 taskId) external returns (uint256 amount);
}

/**
 * @title SealEscrowVault
 * @notice Registry + custody for SEAL receivables. A seal exists in two
 *         fundamentally different states and the distinction is the
 *         security property of this lane:
 *           - REGISTERED: signature verified, nonce consumed, ZERO funds held.
 *             This is a promise, not collateral. It backs NO advance.
 *           - LOCKED: buyer's funds verifiably transferred into this vault
 *             for the taskId, accounted at the amount ACTUALLY RECEIVED
 *             (balance-delta, fee-on-transfer safe). Only this state is
 *             collateral, and only up to the received amount.
 */
contract SealEscrowVault is ISealEscrowVault {
    using SafeERC20 for IERC20;

    IERC20 public immutable override token;
    address public admin;
    /// @notice The only address allowed to release/refund locked funds
    ///         (the SealFactoring contract). Set by admin at wiring time;
    ///         later changes are timelocked (see {setSettlementModule}).
    address public settlementModule;
    /// @notice FIX (finding 2): pending timelocked module change.
    address public pendingSettlementModule;
    uint256 public pendingModuleEffectiveAt;
    /// @notice Delay before a proposed module change can be executed.
    uint256 public constant MODULE_CHANGE_DELAY = 2 days;
    /// @notice Number of receivables currently LOCKED (escrow at risk).
    ///         A module change cannot execute while this is non-zero.
    uint256 public activeLockedCount;

    string public constant SEAL_DOMAIN_NAME = "SINCOR_SEAL";
    string public constant SEAL_DOMAIN_VERSION = "1";
    bytes32 public constant SEAL_TYPEHASH = keccak256(
        "SealAuthorization(bytes32 taskId,address buyer,address provider,uint256 amount,bytes32 expectedSchemaHash,uint256 deadline,uint256 nonce)"
    );
    // secp256k1 n/2 — ECDSA malleability guard (same bound as StakeSlashManager).
    uint256 private constant HALF_N =
        0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0;

    struct Receivable {
        SealAuthorization seal;
        bool exists;
        bool locked;
        bool settled;
        bool failed;
        uint256 lockedAmount; // amount ACTUALLY received at lock (delta-verified)
    }

    mapping(bytes32 => Receivable) private _receivables;
    /// @notice Mirrors the Hub's `nonces(buyer)`: next expected seal nonce.
    mapping(address => uint256) public nonces;

    event SealRegistered(bytes32 indexed taskId, address indexed buyer, address indexed provider, uint256 amount);
    event EscrowLocked(bytes32 indexed taskId, address indexed buyer, uint256 amount);
    event EscrowReleasedForSettlement(bytes32 indexed taskId, address indexed to, uint256 amount);
    event EscrowRefundedOnFailure(bytes32 indexed taskId, address indexed buyer, uint256 amount);
    event EscrowReclaimed(bytes32 indexed taskId, address indexed buyer, uint256 amount);
    event SettlementModuleSet(address indexed module);
    event SettlementModuleProposed(address indexed module, uint256 effectiveAt);
    event SettlementModuleChangeCancelled(address indexed module);

    error Unauthorized();
    error ZeroAddress();
    error SealAlreadyRegistered();
    error SealNotRegistered();
    error SealAlreadyLocked();
    error SealNotLocked();
    error SealAlreadySettled();
    error SealAlreadyFailed();
    error SealExpired();
    error InvalidNonce(uint256 expected, uint256 got);
    error InvalidSealSignature();
    error ZeroAmount();
    error ReceivedZeroAmount();
    error NoPendingModuleChange();
    error ModuleChangeNotReady(uint256 effectiveAt, uint256 currentTime);
    error ModuleChangeBlockedByActiveEscrow(uint256 activeLocked);

    constructor(address _token, address _admin) {
        if (_token == address(0) || _admin == address(0)) revert ZeroAddress();
        token = IERC20(_token);
        admin = _admin;
    }

    /**
     * @notice Set or propose a new settlement module.
     * @dev FIX (finding 2): if no module exists yet this is the initial
     *      wiring and takes effect immediately. Otherwise the call only
     *      PROPOSES the change, effective after {MODULE_CHANGE_DELAY};
     *      {executeSettlementModuleChange} applies it and is blocked while
     *      any escrow remains locked, so a mid-flight repoint can never
     *      drain or strand an active advance.
     */
    function setSettlementModule(address module) external {
        if (msg.sender != admin) revert Unauthorized();
        if (module == address(0)) revert ZeroAddress();
        if (settlementModule == address(0)) {
            settlementModule = module;
            emit SettlementModuleSet(module);
            return;
        }
        pendingSettlementModule = module;
        pendingModuleEffectiveAt = block.timestamp + MODULE_CHANGE_DELAY;
        emit SettlementModuleProposed(module, pendingModuleEffectiveAt);
    }

    /// @notice Execute a previously proposed module change once its delay
    ///         has elapsed AND no escrow is locked. Admin-only.
    function executeSettlementModuleChange() external {
        if (msg.sender != admin) revert Unauthorized();
        address module = pendingSettlementModule;
        if (module == address(0)) revert NoPendingModuleChange();
        if (block.timestamp < pendingModuleEffectiveAt) {
            revert ModuleChangeNotReady(pendingModuleEffectiveAt, block.timestamp);
        }
        if (activeLockedCount > 0) revert ModuleChangeBlockedByActiveEscrow(activeLockedCount);
        settlementModule = module;
        pendingSettlementModule = address(0);
        pendingModuleEffectiveAt = 0;
        emit SettlementModuleSet(module);
    }

    /// @notice Cancel a pending module change. Admin-only.
    function cancelSettlementModuleChange() external {
        if (msg.sender != admin) revert Unauthorized();
        if (pendingSettlementModule == address(0)) revert NoPendingModuleChange();
        emit SettlementModuleChangeCancelled(pendingSettlementModule);
        pendingSettlementModule = address(0);
        pendingModuleEffectiveAt = 0;
    }

    // --- EIP-712 (domain: SINCOR_SEAL / "1" / chainId / this contract) ---

    function domainSeparator() public view returns (bytes32) {
        return keccak256(
            abi.encode(
                keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
                keccak256(bytes(SEAL_DOMAIN_NAME)),
                keccak256(bytes(SEAL_DOMAIN_VERSION)),
                block.chainid,
                address(this)
            )
        );
    }

    function hashSeal(SealAuthorization memory seal) public view returns (bytes32) {
        bytes32 structHash = keccak256(
            abi.encode(
                SEAL_TYPEHASH,
                seal.taskId,
                seal.buyer,
                seal.provider,
                seal.amount,
                seal.expectedSchemaHash,
                seal.deadline,
                seal.nonce
            )
        );
        return keccak256(abi.encodePacked("\x19\x01", domainSeparator(), structHash));
    }

    /**
     * @notice Register a buyer-signed SEAL. Verifies the EIP-712 signature
     *         and consumes the buyer's nonce. MOVES NO FUNDS — after this
     *         call the receivable is a signed promise only, and
     *         {SealFactoring} will refuse to advance against it until
     *         {lockEscrow} has actually pulled the buyer's funds.
     */
    function registerSeal(SealAuthorization calldata seal, uint8 v, bytes32 r, bytes32 s) external {
        if (seal.amount == 0) revert ZeroAmount();
        if (seal.buyer == address(0) || seal.provider == address(0)) revert ZeroAddress();
        if (_receivables[seal.taskId].exists) revert SealAlreadyRegistered();
        if (block.timestamp > seal.deadline) revert SealExpired();
        uint256 expectedNonce = nonces[seal.buyer];
        if (seal.nonce != expectedNonce) revert InvalidNonce(expectedNonce, seal.nonce);
        address signer = _recover(hashSeal(seal), v, r, s);
        if (signer != seal.buyer) revert InvalidSealSignature();

        nonces[seal.buyer] = expectedNonce + 1;
        _receivables[seal.taskId] = Receivable({
            seal: seal, exists: true, locked: false, settled: false, failed: false, lockedAmount: 0
        });
        emit SealRegistered(seal.taskId, seal.buyer, seal.provider, seal.amount);
    }

    /**
     * @notice Lock the buyer's funds for a registered seal. This — and only
     *         this — turns the signature into collateral.
     * @dev FIX (finding 10): the collateral amount is the MEASURED balance
     *      delta (after − before), not the nominal seal amount. With a
     *      fee-on-transfer token the vault accounts exactly what arrived,
     *      so issuance caps and both exits are sized to real holdings and
     *      can never wedge on a phantom shortfall.
     */
    function lockEscrow(bytes32 taskId) external {
        Receivable storage rec = _receivables[taskId];
        if (!rec.exists) revert SealNotRegistered();
        if (rec.locked) revert SealAlreadyLocked();
        if (rec.settled) revert SealAlreadySettled();
        if (rec.failed) revert SealAlreadyFailed();
        if (block.timestamp > rec.seal.deadline) revert SealExpired();

        uint256 balanceBefore = token.balanceOf(address(this));
        rec.locked = true; // effects before interaction
        token.safeTransferFrom(rec.seal.buyer, address(this), rec.seal.amount);
        uint256 received = token.balanceOf(address(this)) - balanceBefore;
        if (received == 0) revert ReceivedZeroAmount();
        rec.lockedAmount = received;
        activeLockedCount += 1;
        emit EscrowLocked(taskId, rec.seal.buyer, received);
    }

    /// @notice Settlement release: only the factoring module, only from the
    ///         LOCKED state. Transfers the full RECEIVED (locked) amount to
    ///         the module, which owns the deduction waterfall.
    function releaseForSettlement(bytes32 taskId, address to) external returns (uint256 amount) {
        if (msg.sender != settlementModule) revert Unauthorized();
        Receivable storage rec = _receivables[taskId];
        if (!rec.exists) revert SealNotRegistered();
        if (!rec.locked) revert SealNotLocked();
        if (rec.settled) revert SealAlreadySettled();
        if (rec.failed) revert SealAlreadyFailed();

        rec.settled = true;
        amount = rec.lockedAmount != 0 ? rec.lockedAmount : rec.seal.amount;
        activeLockedCount -= 1;
        token.safeTransfer(to, amount);
        emit EscrowReleasedForSettlement(taskId, to, amount);
    }

    /// @notice Failure refund: only the factoring module, only from the
    ///         LOCKED state. The receivable dies; the buyer is made whole
    ///         from escrow up to the amount actually received at lock.
    function refundOnFailure(bytes32 taskId) external returns (uint256 amount) {
        if (msg.sender != settlementModule) revert Unauthorized();
        Receivable storage rec = _receivables[taskId];
        if (!rec.exists) revert SealNotRegistered();
        if (!rec.locked) revert SealNotLocked();
        if (rec.settled) revert SealAlreadySettled();
        if (rec.failed) revert SealAlreadyFailed();

        rec.failed = true;
        amount = rec.lockedAmount != 0 ? rec.lockedAmount : rec.seal.amount;
        activeLockedCount -= 1;
        token.safeTransfer(rec.seal.buyer, amount);
        emit EscrowRefundedOnFailure(taskId, rec.seal.buyer, amount);
    }

    /**
     * @notice No-advance reclaim: only the factoring module, only from the
     *         LOCKED state. ROUND-2 FIX (dust-lock finding): the factoring
     *         module calls this ONLY when no advance exists for the task
     *         (its own state check is the gate — this vault function does
     *         not and cannot know advance state, so the module-only caller
     *         restriction is load-bearing). The buyer is made whole up to
     *         the amount ACTUALLY RECEIVED at lock (fee-on-transfer safe,
     *         finding 10 accounting), `activeLockedCount` decrements
     *         exactly once, and the receivable is terminally closed by
     *         setting `failed` — the same terminal flag the failure refund
     *         uses — so every other exit (release / refund / re-lock /
     *         a second reclaim) reverts on its existing settled/failed
     *         guards and can never double-pay or double-decrement.
     *         Effects precede the transfer (checks-effects-interactions),
     *         mirroring {refundOnFailure}.
     */
    function reclaimLockedEscrow(bytes32 taskId) external returns (uint256 amount) {
        if (msg.sender != settlementModule) revert Unauthorized();
        Receivable storage rec = _receivables[taskId];
        if (!rec.exists) revert SealNotRegistered();
        if (!rec.locked) revert SealNotLocked();
        if (rec.settled) revert SealAlreadySettled();
        if (rec.failed) revert SealAlreadyFailed();

        rec.failed = true; // terminal closure — never settleable again
        amount = rec.lockedAmount != 0 ? rec.lockedAmount : rec.seal.amount;
        activeLockedCount -= 1;
        token.safeTransfer(rec.seal.buyer, amount);
        emit EscrowReclaimed(taskId, rec.seal.buyer, amount);
    }

    function getReceivable(bytes32 taskId)
        external
        view
        override
        returns (SealAuthorization memory seal, bool locked, bool settled, bool failed)
    {
        Receivable storage rec = _receivables[taskId];
        return (rec.seal, rec.locked, rec.settled, rec.failed);
    }

    function isLocked(bytes32 taskId) external view override returns (bool) {
        Receivable storage rec = _receivables[taskId];
        return rec.exists && rec.locked && !rec.settled && !rec.failed;
    }

    /// @notice Amount actually held as collateral for a taskId (0 if none).
    function lockedAmountOf(bytes32 taskId) external view override returns (uint256) {
        return _receivables[taskId].lockedAmount;
    }

    function _recover(bytes32 digest, uint8 v, bytes32 r, bytes32 s) internal pure returns (address) {
        if (v != 27 && v != 28) revert InvalidSealSignature();
        if (uint256(s) > HALF_N) revert InvalidSealSignature();
        if (r == bytes32(0) || s == bytes32(0)) revert InvalidSealSignature();
        address signer = ecrecover(digest, v, r, s);
        if (signer == address(0)) revert InvalidSealSignature();
        return signer;
    }
}

/**
 * @title SealFactoring
 * @notice The factor. Issues advances ONLY against LOCKED escrow (sized to
 *         the amount actually received), scopes advance spend to the
 *         issuance-time allowlist snapshot, snapshots the factoring fee at
 *         issuance, takes its repayment + fee first at SIGIL settlement,
 *         and executes the clawback/loss path on task failure. Settlement
 *         requires verifier attestation + settler execution (two distinct
 *         roles). See the file header for the full money path, the deadline
 *         rule, and the residual trust assumptions stated plainly.
 */
contract SealFactoring is ReentrancyGuard {
    using SafeERC20 for IERC20;

    /// @notice Locked platform-fee canon: 500 bps = 5%. NOT a parameter —
    ///         this lane does not touch it.
    uint256 public constant PLATFORM_FEE_BPS = 500;
    uint256 public constant BPS_DENOMINATOR = 10_000;
    /// @notice Hard cap on the (founder-decided) factoring fee, so even a
    ///         compromised admin cannot set a confiscatory fee.
    uint256 public constant MAX_FACTORING_FEE_BPS = 500;
    /// @notice Hard cap on advance rate vs the net-of-platform-fee pool.
    ///         With the fee cap above, max deduction = 90% * 1.05 = 94.5%
    ///         of the net pool, so settlement remainder can never go
    ///         negative by construction.
    uint256 public constant MAX_ADVANCE_BPS = 9_000;
    /// @notice FIX (finding 8): minimum advance size. A 1-wei dust advance
    ///         can no longer squat a taskId and permanently block the real
    ///         advance under the one-advance-per-taskId rule.
    uint256 public constant MIN_ADVANCE = 1_000;
    /// @notice FIX (findings 1/6): window after a buyer opens a failure
    ///         dispute during which the settler/verifier may contest by
    ///         settling; after it lapses uncontested, failure may be
    ///         finalized by anyone.
    uint256 public constant DISPUTE_WINDOW = 2 days;

    ISealEscrowVault public immutable vault;
    IERC20 public immutable token;
    address public immutable treasury; // platform-fee recipient (canon path)
    address public admin;
    /// @notice Settlement EXECUTOR — may execute `settle` only for a
    ///         commitment the `verifier` has attested, and may execute
    ///         failure only to adjudicate a buyer dispute or after the
    ///         seal deadline. The settler alone can no longer settle
    ///         unverified work or unilaterally fail a healthy advance.
    address public settler;
    /// @notice FIX (finding 1, role separation): independent verification
    ///         role. Records the on-chain attestation
    ///         (`attestVerification`) that a SIGIL commitment was verified
    ///         off-chain for a task. Must differ from `settler` (enforced
    ///         in the constructor AND the setters — round-2 fix). Defaults to the admin address at
    ///         construction; production must set it to the off-chain SIGIL
    ///         engine's key, custodied independently of the settler key.
    ///         RESIDUAL TRUST: the verifier's attestation is only as honest
    ///         as the verifier — verifier + settler collusion can still
    ///         settle unverified work (see file header).
    address public verifier;
    /// @notice FOUNDER PRICING DECISION (proposed 20 bps = 0.2%; not canon).
    ///         Applies ONLY to advances issued after a change — in-flight
    ///         advances use their issuance snapshot.
    uint256 public factoringFeeBps;
    /// @notice Advance rate vs net-of-platform-fee pool, in bps.
    uint256 public advanceBps;

    enum AdvanceState {
        None,
        Active,
        Settled,
        Defaulted
    }

    struct Advance {
        AdvanceState state;
        address worker; // == seal.provider
        address buyer; // == seal.buyer
        uint256 grossAmount; // locked (actually received) amount snapshot
        uint256 creditIssued; // total spend credit granted
        uint256 spent; // amount actually paid out to allowlisted payees
        bytes32 sigilCommitment; // recorded at settlement
    }

    mapping(bytes32 => Advance) public advances;
    mapping(address => bool) public isAllowlistedPayee;
    /// @notice FIX (finding 5): factoring fee snapshotted at issuance.
    mapping(bytes32 => uint256) public advanceFeeBpsSnapshot;
    /// @notice FIX (finding 4): allowlist epoch snapshotted at issuance.
    mapping(bytes32 => uint256) public advanceAllowlistEpoch;
    /// @notice FIX (finding 1): verifier attestation per taskId. `settle`
    ///         requires the submitted commitment to equal this value.
    mapping(bytes32 => bytes32) public verifiedCommitment;
    /// @notice FIX (findings 1/6): buyer failure-dispute state per taskId.
    mapping(bytes32 => bool) public failureDisputed;
    mapping(bytes32 => uint256) public failureDisputeOpenedAt;
    /// @notice Monotonic epoch bumped on every allowlist change; per-payee
    ///         history lets spend resolve the issuance-time snapshot.
    uint256 public allowlistEpoch;
    mapping(address => uint256[]) private _payeeEpochHistory;
    mapping(address => bool[]) private _payeeValueHistory;
    /// @notice Worker bonds, seizable on default (first loss-absorption).
    mapping(address => uint256) public bondOf;
    /// @notice Residual clawback debt after bond seizure — the factor's
    ///         booked loss / claim against a defaulting worker. FIX
    ///         (finding 3): any outstanding balance GATES future advances.
    mapping(address => uint256) public clawbackDebtOf;
    /// @notice Sum of unspent credits across active advances. Solvency
    ///         guard: new credit requires unreserved token balance.
    uint256 public totalOutstandingCredit;
    /// @notice Sum of all worker bonds currently held (encumbered balance:
    ///         bonds are not factor liquidity and cannot back new credit).
    uint256 public totalBondsHeld;

    event LiquidityFunded(address indexed funder, uint256 amount);
    event PayeeAllowlisted(address indexed payee, bool allowed);
    event FactoringFeeUpdated(uint256 oldBps, uint256 newBps);
    event AdvanceIssued(bytes32 indexed taskId, address indexed worker, uint256 creditIssued, uint256 maxAdvance);
    event AdvanceSpent(bytes32 indexed taskId, address indexed payee, uint256 amount, uint256 totalSpent);
    event BondPosted(address indexed worker, uint256 amount);
    event VerificationAttested(bytes32 indexed taskId, address indexed verifier, bytes32 sigilCommitment);
    event VerifierUpdated(address indexed oldVerifier, address indexed newVerifier);
    event FailureDisputed(bytes32 indexed taskId, address indexed buyer, uint256 openedAt);
    event AdvanceSettled(
        bytes32 indexed taskId,
        address indexed worker,
        uint256 gross,
        uint256 platformFee,
        uint256 factorRecovery, // spent advance retained by the factor
        uint256 factoringFee,
        uint256 workerRemainder,
        bytes32 sigilCommitment
    );
    event AdvanceDefaulted(
        bytes32 indexed taskId,
        address indexed worker,
        uint256 amountOwed, // spent + factoring fee
        uint256 bondSeized,
        uint256 badDebtBooked
    );
    event EscrowReclaimed(bytes32 indexed taskId, address indexed buyer, uint256 amount);

    error Unauthorized();
    error ZeroAddress();
    error ZeroAmount();
    error EscrowNotLocked();
    error AdvanceAlreadyExists();
    error NoActiveAdvance();
    error AdvanceNotActive();
    error AdvanceExceedsMax(uint256 requested, uint256 maxAdvance);
    error InsufficientLiquidity(uint256 requested, uint256 available);
    error PayeeNotAllowlisted(address payee);
    error SpendExceedsCredit(uint256 requested, uint256 remaining);
    error SigilCommitmentZero();
    error FeeTooHigh(uint256 bps);
    error AdvanceRateTooHigh(uint256 bps);
    error SealExpired();
    error TaskNotYetExpired();
    error SelfDealingBlocked();
    error OutstandingClawbackDebt(address who, uint256 debt);
    error AdvanceBelowMinimum(uint256 amount, uint256 minimum);
    error VerificationRequired();
    error RolesMustBeSeparate();
    error FailureDisputeRequired();
    error FailureAlreadyDisputed();
    error NoFailureDispute();
    error DisputeWindowStillOpen(uint256 finalizeAt);
    error ReclaimBlockedByActiveAdvance();

    constructor(
        address _vault,
        address _treasury,
        address _admin,
        address _settler,
        uint256 _factoringFeeBps,
        uint256 _advanceBps
    ) {
        if (_vault == address(0) || _treasury == address(0) || _admin == address(0) || _settler == address(0)) {
            revert ZeroAddress();
        }
        if (_factoringFeeBps > MAX_FACTORING_FEE_BPS) revert FeeTooHigh(_factoringFeeBps);
        if (_advanceBps == 0 || _advanceBps > MAX_ADVANCE_BPS) revert AdvanceRateTooHigh(_advanceBps);
        // ROUND-2 FIX (constructor role collision): `verifier` is
        // initialized to `_admin` below, so a constructor-supplied
        // `_settler == _admin` would silently collapse the two-key
        // control — one key could both attest and settle. Apply the same
        // RolesMustBeSeparate invariant the setters enforce, at the only
        // other point either role is assigned. (Full assignment audit:
        // `verifier` is written only here and in {setVerifier}, `settler`
        // only here and in {setSettler}; both setters already guard the
        // collision, and there is no admin-rotation setter, so this check
        // closes the last initialization path.)
        if (_settler == _admin) revert RolesMustBeSeparate();
        vault = ISealEscrowVault(_vault);
        token = ISealEscrowVault(_vault).token();
        treasury = _treasury;
        admin = _admin;
        settler = _settler;
        verifier = _admin; // separated from `settler` from the start
        factoringFeeBps = _factoringFeeBps;
        advanceBps = _advanceBps;
    }

    modifier onlyAdmin() {
        if (msg.sender != admin) revert Unauthorized();
        _;
    }

    // --- admin ------------------------------------------------------------

    /// @notice Update the payee allowlist. FIX (finding 4): the change is
    ///         recorded under a new epoch and affects ONLY advances issued
    ///         afterwards; in-flight advances keep their issuance snapshot.
    function setPayeeAllowlist(address payee, bool allowed) external onlyAdmin {
        if (payee == address(0)) revert ZeroAddress();
        allowlistEpoch += 1;
        _payeeEpochHistory[payee].push(allowlistEpoch);
        _payeeValueHistory[payee].push(allowed);
        isAllowlistedPayee[payee] = allowed;
        emit PayeeAllowlisted(payee, allowed);
    }

    /// @notice Admin may retune the factoring fee within the hard cap.
    ///         FIX (finding 5): applies only to FUTURE advances; in-flight
    ///         advances settle/fail at their issuance snapshot.
    function setFactoringFeeBps(uint256 newBps) external onlyAdmin {
        if (newBps > MAX_FACTORING_FEE_BPS) revert FeeTooHigh(newBps);
        emit FactoringFeeUpdated(factoringFeeBps, newBps);
        factoringFeeBps = newBps;
    }

    function setSettler(address newSettler) external onlyAdmin {
        if (newSettler == address(0)) revert ZeroAddress();
        if (newSettler == verifier) revert RolesMustBeSeparate();
        settler = newSettler;
    }

    /// @notice Rotate the verifier. FIX (finding 1): the verifier must
    ///         remain a different address from the settler — one key must
    ///         never be able to both attest and execute a settlement.
    function setVerifier(address newVerifier) external onlyAdmin {
        if (newVerifier == address(0)) revert ZeroAddress();
        if (newVerifier == settler) revert RolesMustBeSeparate();
        emit VerifierUpdated(verifier, newVerifier);
        verifier = newVerifier;
    }

    // --- verification attestation (finding 1) -------------------------------

    /**
     * @notice Record the off-chain SIGIL verification result on-chain.
     *         Verifier-only. Binds this taskId to the exact commitment the
     *         verifier checked; `settle` will accept only that commitment.
     *         RESIDUAL TRUST (plain): this proves the verifier ATTESTED —
     *         the correctness of the off-chain verification itself remains
     *         trusted to the verifier, exactly as the Hub design documents.
     */
    function attestVerification(bytes32 taskId, bytes32 sigilCommitment) external {
        if (msg.sender != verifier) revert Unauthorized();
        if (sigilCommitment == bytes32(0)) revert SigilCommitmentZero();
        if (advances[taskId].state != AdvanceState.Active) revert AdvanceNotActive();
        verifiedCommitment[taskId] = sigilCommitment;
        emit VerificationAttested(taskId, msg.sender, sigilCommitment);
    }

    // --- liquidity & bonds --------------------------------------------------

    /// @notice Fund the factor's liquidity pool (the source of advances).
    function fundLiquidity(uint256 amount) external nonReentrant {
        if (amount == 0) revert ZeroAmount();
        token.safeTransferFrom(msg.sender, address(this), amount);
        emit LiquidityFunded(msg.sender, amount);
    }

    /// @notice Worker posts a bond, seized first on default.
    function postBond(uint256 amount) external nonReentrant {
        if (amount == 0) revert ZeroAmount();
        bondOf[msg.sender] += amount;
        totalBondsHeld += amount;
        token.safeTransferFrom(msg.sender, address(this), amount);
        emit BondPosted(msg.sender, amount);
    }

    /// @notice Unreserved liquidity available to back new credit: contract
    ///         balance minus outstanding spend credit minus held bonds.
    function availableLiquidity() public view returns (uint256) {
        uint256 bal = token.balanceOf(address(this));
        uint256 encumbered = totalOutstandingCredit + totalBondsHeld;
        return bal > encumbered ? bal - encumbered : 0;
    }

    // --- core: advance ------------------------------------------------------

    /**
     * @notice Issue a spend-credit advance against a LOCKED escrow.
     * @dev THE collateral check: `vault.isLocked(taskId)` must be true —
     *      the buyer's funds must verifiably sit in the vault, and the
     *      advance is sized against the amount ACTUALLY RECEIVED
     *      (`lockedAmountOf`, finding 10). A registered but unlocked seal
     *      (signature only) reverts `EscrowNotLocked`: an unlocked
     *      signature alone is NOT collateral.
     *      One advance per taskId, ever (`AdvanceAlreadyExists` also blocks
     *      re-advancing a settled/defaulted task).
     *      FIXES: buyer == provider reverts `SelfDealingBlocked` (finding
     *      3); any outstanding clawback debt of the worker or the buyer
     *      reverts `OutstandingClawbackDebt` (finding 3 — the recycle loop
     *      is closed); amounts below `MIN_ADVANCE` revert
     *      `AdvanceBelowMinimum` (finding 8); the factoring fee and the
     *      allowlist epoch are snapshotted onto the advance (findings 4/5).
     */
    function issueAdvance(bytes32 taskId, uint256 amount) external nonReentrant {
        if (amount == 0) revert ZeroAmount();
        if (advances[taskId].state != AdvanceState.None) revert AdvanceAlreadyExists();
        if (!vault.isLocked(taskId)) revert EscrowNotLocked();

        (SealAuthorization memory seal,,,) = vault.getReceivable(taskId);
        if (seal.buyer == seal.provider) revert SelfDealingBlocked();
        if (block.timestamp > seal.deadline) revert SealExpired();
        uint256 workerDebt = clawbackDebtOf[seal.provider];
        if (workerDebt > 0) revert OutstandingClawbackDebt(seal.provider, workerDebt);
        uint256 buyerDebt = clawbackDebtOf[seal.buyer];
        if (buyerDebt > 0) revert OutstandingClawbackDebt(seal.buyer, buyerDebt);
        if (amount < MIN_ADVANCE) revert AdvanceBelowMinimum(amount, MIN_ADVANCE);

        uint256 lockedAmount = vault.lockedAmountOf(taskId);
        uint256 gross = lockedAmount != 0 ? lockedAmount : seal.amount;
        uint256 maxAdvance = _maxAdvance(gross);
        if (amount > maxAdvance) revert AdvanceExceedsMax(amount, maxAdvance);
        uint256 avail = availableLiquidity();
        if (amount > avail) revert InsufficientLiquidity(amount, avail);

        advances[taskId] = Advance({
            state: AdvanceState.Active,
            worker: seal.provider,
            buyer: seal.buyer,
            grossAmount: gross,
            creditIssued: amount,
            spent: 0,
            sigilCommitment: bytes32(0)
        });
        advanceFeeBpsSnapshot[taskId] = factoringFeeBps;
        advanceAllowlistEpoch[taskId] = allowlistEpoch;
        totalOutstandingCredit += amount;
        emit AdvanceIssued(taskId, seal.provider, amount, maxAdvance);
    }

    // --- core: no-advance reclaim (round-2 fix) ------------------------------

    /**
     * @notice Reclaim a LOCKED escrow for which NO advance was ever issued.
     * @dev ROUND-2 FIX (dust-lock finding): before this exit existed, every
     *      vault exit ran through an Active advance (settle / failure /
     *      expire all require one), so a seal too small to ever satisfy
     *      `MIN_ADVANCE` against its max-advance cap (e.g. gross 500 ->
     *      max advance 380 < 1_000) stranded the buyer's funds forever
     *      and pinned `activeLockedCount` above zero, permanently
     *      blocking the vault's timelocked settlement-module repoint.
     *
     *      Safety — the MINIMAL sufficient rule, deliberately with NO
     *      deadline condition:
     *        - The escrow must still be LOCKED (`EscrowNotLocked`
     *          otherwise — this also makes a second reclaim impossible,
     *          since the first reclaim terminally closes the receivable
     *          in the vault and `isLocked` goes false).
     *        - NO advance may exist for the task, in ANY state
     *          (`ReclaimBlockedByActiveAdvance` if one does — for an
     *          Active advance the name is literal; Settled/Defaulted
     *          advances are unreachable here because their escrow already
     *          exited, but the check is stated over all non-None states
     *          so the reclaim can never interact with an advance
     *          lifecycle at all). The round-1 rule is NOT weakened: while
     *          an advance is active/outstanding, escrow stays locked in
     *          its service and the repoint stays blocked.
     *        - Only the seal's buyer may call (`Unauthorized` otherwise).
     *      No deadline gate is needed because with no advance issued, no
     *      counterparty holds any claim on the escrow: no worker received
     *      credit, nothing was spent, and the buyer is simply withdrawing
     *      their own unencumbered funds. Gating the reclaim on the seal
     *      deadline would re-strand dust locks for the whole seal window
     *      for no protective benefit.
     *      The reclaim amount is the vault's RECEIVED (locked) amount —
     *      fee-on-transfer accounting from finding 10 — moved by the
     *      vault under checks-effects-interactions; this function is
     *      `nonReentrant` and changes no local state before the call.
     */
    function reclaimLockedEscrow(bytes32 taskId) external nonReentrant returns (uint256 amount) {
        if (!vault.isLocked(taskId)) revert EscrowNotLocked();
        if (advances[taskId].state != AdvanceState.None) revert ReclaimBlockedByActiveAdvance();
        (SealAuthorization memory seal,,,) = vault.getReceivable(taskId);
        if (msg.sender != seal.buyer) revert Unauthorized();
        amount = vault.reclaimLockedEscrow(taskId);
        emit EscrowReclaimed(taskId, msg.sender, amount);
    }

    /**
     * @notice Spend advance credit — the ONLY way advanced funds leave.
     *         Caller must be the task's worker; the payee must have been
     *         allowlisted AT ISSUANCE (snapshot, finding 4); amount must
     *         fit the remaining credit; and the spend must occur on or
     *         before the seal deadline (finding 7).
     */
    function spendAdvance(bytes32 taskId, address payee, uint256 amount) external nonReentrant {
        Advance storage adv = advances[taskId];
        if (adv.state != AdvanceState.Active) revert AdvanceNotActive();
        if (msg.sender != adv.worker) revert Unauthorized();
        if (amount == 0) revert ZeroAmount();
        (SealAuthorization memory seal,,,) = vault.getReceivable(taskId);
        if (block.timestamp > seal.deadline) revert SealExpired();
        if (!_wasAllowlisted(payee, advanceAllowlistEpoch[taskId])) revert PayeeNotAllowlisted(payee);
        uint256 remaining = adv.creditIssued - adv.spent;
        if (amount > remaining) revert SpendExceedsCredit(amount, remaining);

        adv.spent += amount;
        totalOutstandingCredit -= amount;
        token.safeTransfer(payee, amount);
        emit AdvanceSpent(taskId, payee, amount, adv.spent);
    }

    /// @dev Was `payee` allowlisted as of allowlist epoch `epoch`?
    function _wasAllowlisted(address payee, uint256 epoch) internal view returns (bool) {
        uint256[] storage epochs = _payeeEpochHistory[payee];
        bool[] storage values = _payeeValueHistory[payee];
        for (uint256 i = epochs.length; i > 0; i--) {
            if (epochs[i - 1] <= epoch) return values[i - 1];
        }
        return false;
    }

    // --- core: settlement ---------------------------------------------------

    /**
     * @notice SIGIL settlement of the underlying task.
     * @dev FIX (finding 1, role separation): callable only by the `settler`
     *      AND only for the exact commitment the independent `verifier`
     *      attested via {attestVerification} (`VerificationRequired`
     *      otherwise). The settler alone can no longer settle unverified
     *      work. RESIDUAL TRUST: verifier + settler collusion can still
     *      settle unverified work — verification correctness is off-chain
     *      by documented design (see file header).
     *      FIX (finding 7): settlement requires
     *      `block.timestamp <= seal.deadline` (`SealExpired` after).
     *      FIX (finding 5): the factoring fee uses the issuance snapshot.
     *      FIX (finding 9): the platform fee uses ceil division.
     *      A pending buyer failure-dispute does NOT block settlement —
     *      settling inside the dispute window is exactly how the settler
     *      contests the dispute.
     *
     *      Waterfall on the locked (received) gross G:
     *        platformFee    = ceil(G * 500 / 10_000)        -> treasury
     *        factoringFee   = spent * snapshotFeeBps / 10_000
     *        factorRecovery = spent (advance principal)     -> retained
     *        workerRemainder = G - platformFee - spent - factoringFee
     */
    function settle(bytes32 taskId, bytes32 sigilCommitment) external nonReentrant {
        if (msg.sender != settler) revert Unauthorized();
        if (sigilCommitment == bytes32(0)) revert SigilCommitmentZero();
        Advance storage adv = advances[taskId];
        if (adv.state == AdvanceState.None) revert NoActiveAdvance();
        if (adv.state != AdvanceState.Active) revert AdvanceNotActive();
        (SealAuthorization memory seal,,,) = vault.getReceivable(taskId);
        if (block.timestamp > seal.deadline) revert SealExpired();
        if (verifiedCommitment[taskId] != sigilCommitment) revert VerificationRequired();

        uint256 gross = vault.releaseForSettlement(taskId, address(this));

        uint256 platformFee = _platformFee(gross);
        uint256 spent = adv.spent;
        uint256 factoringFee = (spent * advanceFeeBpsSnapshot[taskId]) / BPS_DENOMINATOR;
        uint256 workerRemainder = gross - platformFee - spent - factoringFee;

        // Cancel the unspent credit reservation.
        totalOutstandingCredit -= (adv.creditIssued - spent);

        adv.state = AdvanceState.Settled;
        adv.sigilCommitment = sigilCommitment;
        failureDisputed[taskId] = false;

        if (platformFee > 0) token.safeTransfer(treasury, platformFee);
        if (workerRemainder > 0) token.safeTransfer(adv.worker, workerRemainder);
        // spent + factoringFee stay in this contract: advance principal
        // returns to the liquidity pool, the fee is factor revenue.
        emit AdvanceSettled(taskId, adv.worker, gross, platformFee, spent, factoringFee, workerRemainder, sigilCommitment);
    }

    // --- core: failure / clawback ------------------------------------------

    /**
     * @notice Failure entry point — semantics split by caller (FIXES
     *         findings 1/6):
     *           - BUYER: does NOT execute failure. It only OPENS a failure
     *             dispute (records the window), giving the settler/verifier
     *             {DISPUTE_WINDOW} to contest by settling, and the settler
     *             the option to adjudicate immediately (below). A buyer can
     *             no longer unilaterally seize the worker's bond and refund
     *             itself after consuming the work.
     *           - SETTLER: EXECUTES failure, but only to adjudicate an open
     *             buyer dispute, or after the seal deadline. A settler can
     *             no longer unilaterally fail a healthy, undisputed,
     *             pre-deadline advance on its word alone.
     *           - Anyone else: `Unauthorized`.
     *         After the dispute window lapses uncontested, see
     *         {finalizeFailure}; after the deadline, see {expire}.
     */
    function markFailed(bytes32 taskId) external nonReentrant {
        Advance storage adv = advances[taskId];
        if (adv.state != AdvanceState.Active) revert AdvanceNotActive();
        if (msg.sender == adv.buyer) {
            if (failureDisputed[taskId]) revert FailureAlreadyDisputed();
            failureDisputed[taskId] = true;
            failureDisputeOpenedAt[taskId] = block.timestamp;
            emit FailureDisputed(taskId, msg.sender, block.timestamp);
            return;
        }
        if (msg.sender == settler) {
            (SealAuthorization memory seal,,,) = vault.getReceivable(taskId);
            if (!failureDisputed[taskId] && block.timestamp <= seal.deadline) {
                revert FailureDisputeRequired();
            }
            _executeFailure(taskId);
            return;
        }
        revert Unauthorized();
    }

    /// @notice Finalize a buyer failure dispute once {DISPUTE_WINDOW} has
    ///         lapsed without a settlement contesting it. Permissionless.
    function finalizeFailure(bytes32 taskId) external nonReentrant {
        Advance storage adv = advances[taskId];
        if (adv.state != AdvanceState.Active) revert AdvanceNotActive();
        if (!failureDisputed[taskId]) revert NoFailureDispute();
        uint256 finalizeAt = failureDisputeOpenedAt[taskId] + DISPUTE_WINDOW;
        if (block.timestamp < finalizeAt) revert DisputeWindowStillOpen(finalizeAt);
        _executeFailure(taskId);
    }

    /// @notice Permissionless failure path once the seal deadline has
    ///         passed without settlement: anyone may trigger the clawback.
    function expire(bytes32 taskId) external nonReentrant {
        Advance storage adv = advances[taskId];
        if (adv.state != AdvanceState.Active) revert AdvanceNotActive();
        (SealAuthorization memory seal,,,) = vault.getReceivable(taskId);
        if (block.timestamp <= seal.deadline) revert TaskNotYetExpired();
        _executeFailure(taskId);
    }

    /**
     * @dev Failure / clawback execution:
     *        1. Vault refunds the locked (received) gross to the buyer.
     *        2. Unspent credit is cancelled (it never left the factor).
     *        3. Amount owed = spent advance + factoring fee on the spent
     *           part AT THE ISSUANCE SNAPSHOT RATE (finding 5).
     *        4. The worker's bond is seized up to the amount owed.
     *        5. Any residual is booked as `clawbackDebtOf[worker]`, which
     *           now GATES all future advances for that worker (finding 3).
     */
    function _executeFailure(bytes32 taskId) internal {
        Advance storage adv = advances[taskId];
        address worker = adv.worker;
        uint256 spent = adv.spent;

        vault.refundOnFailure(taskId);
        totalOutstandingCredit -= (adv.creditIssued - spent);

        uint256 factoringFee = (spent * advanceFeeBpsSnapshot[taskId]) / BPS_DENOMINATOR;
        uint256 owed = spent + factoringFee;

        uint256 bond = bondOf[worker];
        uint256 seized = bond < owed ? bond : owed;
        if (seized > 0) {
            bondOf[worker] = bond - seized;
            totalBondsHeld -= seized;
            // The seized bond is already in this contract's balance; the
            // seizure is a ledger move — those tokens now offset the loss
            // as factor-recovered funds instead of held bond.
        }
        uint256 badDebt = owed - seized;
        if (badDebt > 0) clawbackDebtOf[worker] += badDebt;

        adv.state = AdvanceState.Defaulted;
        failureDisputed[taskId] = false;
        emit AdvanceDefaulted(taskId, worker, owed, seized, badDebt);
    }

    // --- views ----------------------------------------------------------------

    /// @dev FIX (finding 9): platform fee with CEIL division — a gross > 0
    ///      always yields a fee of at least 1 wei instead of silently
    ///      truncating to 0 on tiny seals. The RATE is unchanged (500 bps
    ///      canon); amounts divisible by the denominator are exact, as
    ///      before.
    function _platformFee(uint256 gross) internal pure returns (uint256) {
        return (gross * PLATFORM_FEE_BPS + BPS_DENOMINATOR - 1) / BPS_DENOMINATOR;
    }

    function _maxAdvance(uint256 gross) internal view returns (uint256) {
        uint256 netPool = gross - _platformFee(gross);
        return (netPool * advanceBps) / BPS_DENOMINATOR;
    }

    /// @notice Max advance currently issuable for a task's gross amount.
    function maxAdvanceFor(uint256 gross) external view returns (uint256) {
        return _maxAdvance(gross);
    }

    /// @notice Factoring fee the CURRENT parameter would charge on an
    ///         amount. Note: in-flight advances are charged at their
    ///         issuance snapshot (`advanceFeeBpsSnapshot`), not this value.
    function factoringFeeOn(uint256 spentAmount) external view returns (uint256) {
        return (spentAmount * factoringFeeBps) / BPS_DENOMINATOR;
    }
}
