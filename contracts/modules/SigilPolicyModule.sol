// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";

/// @dev ERC-7579 core module interface (EIP-7579, "Modules" section).
///      Defined in-file, mirroring the pattern already used by
///      contracts/security/SincorSessionValidator.sol in this repo.
interface IERC7579Module {
    function onInstall(bytes calldata data) external;
    function onUninstall(bytes calldata data) external;
    function isModuleType(uint256 moduleTypeId) external view returns (bool);
}

/// @dev ERC-7579 hook interface (EIP-7579, "Hooks" section, module type id 4).
interface IERC7579Hook is IERC7579Module {
    function preCheck(address msgSender, uint256 value, bytes calldata msgData)
        external
        returns (bytes memory hookData);
    function postCheck(bytes calldata hookData) external;
}

/// @dev Interface to the SINCOR Settlement Hub, aligned EXACTLY to the Hub
///      draft (founder spec drop 2026-11-11, SincorSettlementHub.sol — the
///      Hub is the source of truth). The hook only ever CALLS
///      `isTaskVerified(bytes32, bytes32)`. The draft hook sketch declared a
///      `verifyAndSettleSeal(bytes32,bytes32,bytes)` that does not exist on
///      the Hub; the Hub's real signature is the 5-argument form below
///      (settlement is the facilitator's call, never the hook's).
interface ISincorSettlementHub {
    struct SealAuthorization {
        bytes32 taskId;
        address buyer;
        address provider;
        uint256 amount;
        bytes32 expectedSchemaHash;
        uint256 deadline;
        uint256 nonce;
    }

    function isTaskVerified(bytes32 taskId, bytes32 expectedSigil) external view returns (bool);

    function verifyAndSettleSeal(
        SealAuthorization calldata seal,
        bytes32 sigilCommitment,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external returns (bool);
}

/// @title SigilPolicyModule — ERC-7579 pre-settlement policy module CANDIDATE
/// @notice A module candidate to co-develop with ZeroDev. It is NOT a deployed
///         product and has never been installed on a production account.
///
/// @dev    Job: gate fund release from a modular smart account on a valid
///         SIGIL attestation. Before a session-key operation may execute a
///         fund-releasing call, a SIGIL verifier must have produced an
///         EIP-712 signed commitment binding:
///           (account, task spec hash, result commitment, payee, token,
///            amount cap, expiry, nonce)
///         The attestation is presented ahead of execution via
///         `registerAttestation` (anyone may relay it; security rests on the
///         verifier signature, not the relayer). When the account later runs
///         the payment through `execute`, its hook `preCheck` matches the
///         pending attestation against the actual (payee, token, amount),
///         consumes it (single-use), and only then lets execution proceed.
///
///         Failure modes — all blocked:
///           - missing:   no pending attestation matches the payment
///           - expired:   attestation expiry has passed (checked at both
///                        registration and consumption)
///           - replayed:  digest or nonce already used (registration of a
///                        consumed attestation reverts; preCheck consumes)
///           - wrong signer: EIP-712 signature does not recover to the
///                        verifier configured for the account at install
///           - over cap:  payment amount exceeds the attestation amountCap,
///                        or the attestation amountCap exceeds the per-account
///                        policy cap fixed at install time
///
///         W-148 (hunt 47): "release" also covers allowance grants
///         (approve / increaseAllowance / permit) and calls into contracts
///         holding a hook-tracked allowance — both move funds out of the
///         account without a transfer selector ever reaching the hook.
///         For grants and holder calls the attestation's `payee` is the
///         spender / holder being exposed to the funds.
///
///         W-149 (hunt 47): onUninstall deletes ALL account-scoped state
///         (pending attestations, used digests/nonces, allowance tracking,
///         config), so rotating a verifier via uninstall + reinstall
///         actually revokes everything the old verifier signed.
///
///         Scope honesty: this module gates RELEASE on a verification
///         attestation only. It does not slash, does not hold stake, and does
///         not decide whether work was actually good — the SIGIL verifier
///         makes that judgement off-chain / in its own system, and SINCOR's
///         ghost-slashing enforcement lives in stake_ledger +
///         ExecutionEscrowManager, not in any validation/hook module.
///
///         HOW THE ATTESTATION REACHES THE HOOK (design choice, and why):
///         ERC-7579 hooks receive only (msgSender, value, msgData), where
///         msgData is the execution calldata owned by the account/execute
///         standards — there is NO standard footer/trailer convention for
///         smuggling (taskId, commitment) pairs into it (the founder
///         spec-drop draft hook's 64-byte footer is invented; appending
///         foreign bytes would corrupt the inner call or be stripped by
///         bundlers/accounts). Per-account install data can't carry it
///         either: install happens once, commitments are per-task. So this
///         module keeps an attestation REGISTRY keyed by
///         (account, token, payee): the verifier-signed attestation is
///         presented ahead of execution via `registerAttestation`, and
///         preCheck matches it against the (token, payee, amount) it
///         DECODES from the actual execution — the hook verifies against
///         the real payment values, not against a caller-supplied
///         reference. The alternative for Kernel's validation phase (a
///         type-5 Policy / type-9 PreValidationHook reading the UserOp
///         signature tail, the Kernel permission convention) is listed as
///         co-development work in the build report.
///
///         Interface grounding (verified 2026-11-11 against official sources):
///           - EIP-7579 defines module types 1 Validator / 2 Executor /
///             3 Fallback / 4 Hook, `IERC7579Module`
///             (onInstall/onUninstall/isModuleType) and `IERC7579Hook`
///             (preCheck/postCheck). Single-call executions are packed as
///             abi.encodePacked(target, value, callData) — preCheck below
///             decodes exactly that encoding.
///           - ERC-7780 extends types: 5 Policy / 6 Signer /
///             7 StatelessValidator / 8 PreValidationHookERC1271 /
///             9 PreValidationHookERC4337 / 10 StatelessValidatorWithSender.
///           - ZeroDev Kernel v3 implements types 1-6 (Constants.sol) and
///             attaches one hook per validator / executor / selector
///             (HookManager); hooks run pre/post execution via executeUserOp.
///           - NO official source defines a module type 11. A dev-watch note
///             claiming "hook changed type-4 -> type-11" could not be
///             corroborated; this module codes against type 4 as specified.
contract SigilPolicyModule is IERC7579Hook, EIP712 {
    uint256 public constant MODULE_TYPE_HOOK = 4;

    bytes32 public constant ATTESTATION_TYPEHASH = keccak256(
        "SigilAttestation(address account,bytes32 taskSpecHash,bytes32 resultCommitment,address payee,address token,uint256 amountCap,uint64 expiry,uint256 nonce)"
    );

    bytes4 internal constant ERC20_TRANSFER = 0xa9059cbb; // transfer(address,uint256)
    bytes4 internal constant ERC20_TRANSFER_FROM = 0x23b872dd; // transferFrom(address,address,uint256)
    // W-148: allowance-granting selectors are fund releases in substance —
    // an approve() hands a third party the power to pull funds OUT of the
    // account via transferFrom without this hook ever seeing a transfer.
    bytes4 internal constant ERC20_APPROVE = 0x095ea7b3; // approve(address,uint256)
    bytes4 internal constant ERC20_INCREASE_ALLOWANCE = 0x39509351; // increaseAllowance(address,uint256)
    bytes4 internal constant ERC20_DECREASE_ALLOWANCE = 0xa457c2d7; // decreaseAllowance(address,uint256)
    bytes4 internal constant ERC2612_PERMIT = 0xd505accf; // permit(address,address,uint256,uint256,uint8,bytes32,bytes32)

    /// @notice A SIGIL verification commitment, signed by the account's
    ///         configured verifier key via EIP-712 (SEAL-style typed data).
    struct SigilAttestation {
        address account; // smart account the release is gated for
        bytes32 taskSpecHash; // hash of the agreed task specification
        bytes32 resultCommitment; // commitment to the delivered result
        address payee; // only this payee may be paid under this attestation
        address token; // address(0) = native asset, otherwise the ERC20
        uint256 amountCap; // maximum amount releasable under this attestation
        uint64 expiry; // unix seconds; attestation is dead at/after this time
        uint256 nonce; // per-account single-use nonce (replay protection)
    }

    struct AccountConfig {
        address verifier; // SIGIL verifier key for this account
        uint256 maxCap; // hard per-attestation policy cap fixed at install
        uint256 unverifiedThreshold; // releases at/below this DECODED amount
            // (raw units of the asset being moved) pass without an
            // attestation; anything above it requires one. Compared against
            // the amount decoded from the execution calldata for ERC20
            // transfer/transferFrom — NEVER against msgValue, which is 0
            // for token moves (the founder spec-drop draft hook's bug).
        address settlementHub; // optional SincorSettlementHub; when set,
            // preCheck additionally requires a recorded SIGIL commitment
            // for the attestation's task (see trust-model note below)
        bool initialized;
    }

    struct PendingAttestation {
        bytes32 digest;
        bytes32 taskSpecHash;
        bytes32 resultCommitment;
        uint256 amountCap;
        uint64 expiry;
        uint256 nonce;
        bool exists;
    }

    mapping(address => AccountConfig) public configOf;
    /// account => token => payee => pending attestation (one outstanding
    /// attestation per (account, token, payee); a fresh registration for the
    /// same triple replaces an expired one, never a live one)
    mapping(address => mapping(address => mapping(address => PendingAttestation))) public pendingOf;
    /// account => digest => consumed/registered-before (replay protection)
    mapping(address => mapping(bytes32 => bool)) public usedDigest;
    /// account => nonce => used (replay protection)
    mapping(address => mapping(uint256 => bool)) public usedNonce;

    // ------------------------------------------------------------------
    // W-148 allowance tracking. Every allowance the account grants THROUGH
    // a hook-gated execution is recorded here, so that (a) the grant itself
    // is gated as a release (see _preCheckGrant) and (b) a later call INTO
    // a contract holding an allowance is also gated as a release — the
    // holder can pull via transferFrom inside that call without this hook
    // ever seeing a transfer/transferFrom selector (see
    // _gateAllowanceHolderCall). Allowances granted OUTSIDE hook-gated
    // executions (before install, or via a path this hook does not gate)
    // are not visible to the hook and remain an install-time concern.
    // ------------------------------------------------------------------
    /// account => token => spender => allowance granted through this hook
    mapping(address => mapping(address => mapping(address => uint256))) public allowanceOf;
    /// account => spender => true while the spender holds any tracked
    /// allowance > 0 on any token
    mapping(address => mapping(address => bool)) public isAllowanceHolder;
    /// account => spender => tokens with a tracked allowance > 0
    mapping(address => mapping(address => address[])) internal _holderTokensOf;
    /// account => spender => token => (index in _holderTokensOf + 1)
    mapping(address => mapping(address => mapping(address => uint256))) internal _holderTokenIndex;
    /// account => spenders holding any tracked allowance
    mapping(address => address[]) internal _holderSpendersOf;
    /// account => spender => (index in _holderSpendersOf + 1)
    mapping(address => mapping(address => uint256)) internal _holderSpenderIndex;

    // ------------------------------------------------------------------
    // W-149 enumerable account-scoped state. Mappings are not enumerable,
    // so the module keeps index lists of every pending-attestation key,
    // digest, and nonce it records for an account; onUninstall walks the
    // lists and deletes EVERYTHING, so no attestation, replay marker, or
    // allowance survives an uninstall (e.g. a verifier-key rotation).
    // ------------------------------------------------------------------
    struct PendingKey {
        address token;
        address payee;
    }

    /// account => live pending-attestation keys
    mapping(address => PendingKey[]) internal _pendingKeysOf;
    /// account => keccak256(token, payee) => (index in _pendingKeysOf + 1)
    mapping(address => mapping(bytes32 => uint256)) internal _pendingKeyIndex;
    /// account => every attestation digest ever registered (incl. consumed)
    mapping(address => bytes32[]) internal _digestsOf;
    /// account => every attestation nonce ever registered (incl. consumed)
    mapping(address => uint256[]) internal _noncesOf;

    event ModuleInstalled(
        address indexed account, address indexed verifier, uint256 maxCap, uint256 unverifiedThreshold, address settlementHub
    );
    event ModuleUninstalled(address indexed account);
    event AttestationRegistered(
        address indexed account, bytes32 indexed digest, address indexed payee, address token, uint256 amountCap, uint64 expiry, uint256 nonce
    );
    event AttestationConsumed(address indexed account, bytes32 indexed digest, address indexed payee, address token, uint256 amount);
    event AttestationRevoked(address indexed account, bytes32 indexed digest);

    error AlreadyInstalled(address account);
    error NotInstalled(address account);
    error InvalidVerifier();
    error InvalidCap();
    error WrongSigner(address recovered);
    error AttestationExpired(uint64 expiry, uint256 nowTs);
    error AttestationReplayed(bytes32 digest);
    error NonceReplayed(address account, uint256 nonce);
    error OverPolicyCap(uint256 amountCap, uint256 maxCap);
    error OverAttestationCap(uint256 amount, uint256 amountCap);
    error NoValidAttestation(address account, address token, address payee);
    error PendingAttestationLive(address account, address token, address payee);
    error MalformedExecution();
    error CommitmentNotRecorded(bytes32 taskId);

    constructor() EIP712("SINCOR SIGIL Policy Module", "1") {}

    // ------------------------------------------------------------------
    // ERC-7579 module lifecycle
    // ------------------------------------------------------------------

    /// @notice Install data: abi.encode(address verifier, uint256 maxCap,
    ///         uint256 unverifiedThreshold, address settlementHub).
    ///         settlementHub may be address(0) (recorded-commitment check off).
    ///         msg.sender is the smart account installing the module.
    /// @dev    Install cost note (MoebiusX lesson, 2026-11-11): the first
    ///         session operation that installs a validator/hook pays the
    ///         module's onInstall SSTOREs inside EntryPoint validation —
    ///         for SINCOR's CallPolicy V0_0_4 that measured 2,486,476 gas
    ///         against a 2,000,000 hardcoded bundler limit and starved every
    ///         op until a two-tier gas fix shipped. This onInstall writes
    ///         three storage slots (verifier, maxCap, initialized flag in
    ///         one struct slot where packing allows) and MUST be budgeted
    ///         at the install tier (3.5M verification gas in the MoebiusX
    ///         kit), never at steady-state tier.
    function onInstall(bytes calldata data) external override {
        address account = msg.sender;
        if (configOf[account].initialized) revert AlreadyInstalled(account);
        (address verifier, uint256 maxCap, uint256 unverifiedThreshold, address settlementHub) =
            abi.decode(data, (address, uint256, uint256, address));
        if (verifier == address(0)) revert InvalidVerifier();
        if (maxCap == 0) revert InvalidCap();
        configOf[account] = AccountConfig({
            verifier: verifier,
            maxCap: maxCap,
            unverifiedThreshold: unverifiedThreshold,
            settlementHub: settlementHub,
            initialized: true
        });
        emit ModuleInstalled(account, verifier, maxCap, unverifiedThreshold, settlementHub);
    }

    /// @notice Uninstall wipes ALL account-scoped state (W-149): config,
    ///         every pending attestation, every used-digest / used-nonce
    ///         replay marker, and all W-148 allowance tracking. Before this
    ///         fix only the config was deleted, so a pending attestation
    ///         signed by a rotated-away (e.g. compromised) verifier stayed
    ///         live AND consumable under the new config, and its stale
    ///         entry blocked fresh registrations for the same
    ///         (token, payee). After uninstall the account's state in this
    ///         module is exactly as if it had never installed.
    function onUninstall(bytes calldata) external override {
        address account = msg.sender;
        if (!configOf[account].initialized) revert NotInstalled(account);

        PendingKey[] storage keys = _pendingKeysOf[account];
        for (uint256 i = 0; i < keys.length; i++) {
            delete pendingOf[account][keys[i].token][keys[i].payee];
            delete _pendingKeyIndex[account][_pendingKeyHash(keys[i].token, keys[i].payee)];
        }
        delete _pendingKeysOf[account];

        bytes32[] storage digests = _digestsOf[account];
        for (uint256 i = 0; i < digests.length; i++) {
            delete usedDigest[account][digests[i]];
        }
        delete _digestsOf[account];

        uint256[] storage nonces = _noncesOf[account];
        for (uint256 i = 0; i < nonces.length; i++) {
            delete usedNonce[account][nonces[i]];
        }
        delete _noncesOf[account];

        address[] storage spenders = _holderSpendersOf[account];
        for (uint256 i = 0; i < spenders.length; i++) {
            address spender = spenders[i];
            address[] storage toks = _holderTokensOf[account][spender];
            for (uint256 j = 0; j < toks.length; j++) {
                delete allowanceOf[account][toks[j]][spender];
                delete _holderTokenIndex[account][spender][toks[j]];
            }
            delete _holderTokensOf[account][spender];
            delete isAllowanceHolder[account][spender];
            delete _holderSpenderIndex[account][spender];
        }
        delete _holderSpendersOf[account];

        delete configOf[account];
        emit ModuleUninstalled(account);
    }

    function isModuleType(uint256 moduleTypeId) external pure override returns (bool) {
        return moduleTypeId == MODULE_TYPE_HOOK;
    }

    function isInitialized(address account) external view returns (bool) {
        return configOf[account].initialized;
    }

    // ------------------------------------------------------------------
    // Attestation presentation (SEAL: EIP-712 signed commitment)
    // ------------------------------------------------------------------

    /// @notice Present a SIGIL attestation for later consumption by preCheck.
    ///         Callable by anyone (verifier service, account, relayer): the
    ///         EIP-712 signature from the configured verifier is the security.
    /// @return digest The EIP-712 digest of the attestation.
    function registerAttestation(SigilAttestation calldata att, bytes calldata signature)
        external
        returns (bytes32 digest)
    {
        AccountConfig memory cfg = configOf[att.account];
        if (!cfg.initialized) revert NotInstalled(att.account);
        if (att.amountCap == 0) revert InvalidCap();
        if (att.amountCap > cfg.maxCap) revert OverPolicyCap(att.amountCap, cfg.maxCap);
        if (block.timestamp >= att.expiry) revert AttestationExpired(att.expiry, block.timestamp);

        digest = attestationDigest(att);
        if (usedDigest[att.account][digest]) revert AttestationReplayed(digest);
        if (usedNonce[att.account][att.nonce]) revert NonceReplayed(att.account, att.nonce);

        (address recovered, ECDSA.RecoverError err,) = ECDSA.tryRecover(digest, signature);
        if (err != ECDSA.RecoverError.NoError || recovered != cfg.verifier) {
            revert WrongSigner(recovered);
        }

        PendingAttestation storage pending = pendingOf[att.account][att.token][att.payee];
        if (pending.exists && block.timestamp < pending.expiry) {
            revert PendingAttestationLive(att.account, att.token, att.payee);
        }
        // Replacing an expired pending attestation frees its nonce/digest
        // bookkeeping is intentionally NOT done: expired digests stay marked
        // used so an expired attestation can never be re-presented.
        bool slotWasEmpty = !pending.exists;

        pendingOf[att.account][att.token][att.payee] = PendingAttestation({
            digest: digest,
            taskSpecHash: att.taskSpecHash,
            resultCommitment: att.resultCommitment,
            amountCap: att.amountCap,
            expiry: att.expiry,
            nonce: att.nonce,
            exists: true
        });
        usedDigest[att.account][digest] = true;
        usedNonce[att.account][att.nonce] = true;

        // W-149 index lists: onUninstall walks these to wipe every
        // account-scoped entry (mappings are not enumerable on their own).
        if (slotWasEmpty) _addPendingKey(att.account, att.token, att.payee);
        _digestsOf[att.account].push(digest);
        _noncesOf[att.account].push(att.nonce);

        emit AttestationRegistered(att.account, digest, att.payee, att.token, att.amountCap, att.expiry, att.nonce);
    }

    /// @notice The account may revoke its own outstanding attestation.
    function revokeAttestation(address token, address payee) external {
        address account = msg.sender;
        PendingAttestation storage pending = pendingOf[account][token][payee];
        if (!pending.exists) revert NoValidAttestation(account, token, payee);
        bytes32 digest = pending.digest;
        delete pendingOf[account][token][payee];
        _removePendingKey(account, token, payee);
        emit AttestationRevoked(account, digest);
    }

    /// @notice EIP-712 digest of an attestation under this module's domain.
    function attestationDigest(SigilAttestation calldata att) public view returns (bytes32) {
        return _hashTypedDataV4(_structHash(att));
    }

    function _structHash(SigilAttestation calldata att) internal pure returns (bytes32) {
        return keccak256(
            abi.encode(
                ATTESTATION_TYPEHASH,
                att.account,
                att.taskSpecHash,
                att.resultCommitment,
                att.payee,
                att.token,
                att.amountCap,
                att.expiry,
                att.nonce
            )
        );
    }

    // ------------------------------------------------------------------
    // ERC-7579 hook: the pre-settlement gate
    // ------------------------------------------------------------------

    /// @notice Called by the smart account (msg.sender) before execution.
    /// @dev    `msgData` is the ERC-7579 single-call packed execution:
    ///         abi.encodePacked(target, value, innerCallData). A call is a
    ///         fund release iff ANY of:
    ///           (a) value > 0 (native), or innerCallData is an ERC20
    ///               transfer / transferFrom out of the account;
    ///           (b) W-148: innerCallData GRANTS or grows an allowance over
    ///               the account's tokens (approve / increaseAllowance /
    ///               EIP-2612 permit with owner == account) — the grant is
    ///               gated on the RESULTING allowance, with the spender in
    ///               the attestation's `payee` slot;
    ///           (c) W-148: the call target is a contract holding a tracked
    ///               allowance from the account — it can pull funds via
    ///               transferFrom inside the call, so the call is gated per
    ///               token on the outstanding allowance.
    ///         Non-release calls are not gated and return empty hookData.
    ///         A release is allowed only against a pending, unexpired
    ///         attestation whose (token, payee) match and whose amountCap
    ///         covers the amount; the attestation is consumed here
    ///         (single-use), so a replayed execution finds nothing and
    ///         reverts.
    ///         Trade-off, stated plainly: consumption happens in preCheck, so
    ///         if the execution itself later fails, the attestation is still
    ///         burned and the verifier must issue a fresh one. That is the
    ///         safe direction (never double-release) and matches how
    ///         Kernel-style hooks behave when execution reverts after the
    ///         hook has run in a separate executeUserOp phase.
    function preCheck(address, uint256, bytes calldata msgData) external override returns (bytes memory hookData) {
        address account = msg.sender;
        AccountConfig memory cfg = configOf[account];
        if (!cfg.initialized) revert NotInstalled(account);
        if (msgData.length < 52) revert MalformedExecution();
        address target = address(bytes20(msgData[0:20]));
        uint256 value = uint256(bytes32(msgData[20:52]));
        bytes calldata inner = msgData[52:];

        // W-148 (b): allowance-granting calls are gated as releases and the
        // grant is recorded for holder-call gating and uninstall wipe.
        if (value == 0 && inner.length >= 4) {
            bytes4 selector = bytes4(inner[0:4]);
            if (
                selector == ERC20_APPROVE || selector == ERC20_INCREASE_ALLOWANCE
                    || selector == ERC20_DECREASE_ALLOWANCE || selector == ERC2612_PERMIT
            ) {
                return _preCheckGrant(account, cfg, target, selector, inner);
            }
        }

        (address token, address payee, uint256 amount, bool isRelease) = _decodeRelease(account, msgData);
        if (!isRelease) {
            // W-148 (c): a call into an allowance holder can pull funds via
            // transferFrom without showing this hook a transfer selector.
            if (isAllowanceHolder[account][target]) {
                return _gateAllowanceHolderCall(account, cfg, target);
            }
            return bytes("");
        }

        // Unverified threshold, applied to the DECODED amount. For an ERC20
        // transfer/transferFrom execution the ETH msgValue is 0, so any
        // threshold keyed on msgValue (as in the founder spec-drop draft
        // hook) would let arbitrarily large token settlements through
        // ungated. Here `amount` is the token amount decoded from the
        // execution calldata (raw units of the moved asset).
        if (amount <= cfg.unverifiedThreshold) return bytes("");

        bytes32 digest = _consumeAttestation(account, cfg, token, payee, amount);
        return abi.encode(digest);
    }

    /// @dev Gate one release of `amount` of `token` to `payee` against the
    ///      pending attestation for (account, token, payee) and consume it.
    ///      Shared by direct releases, allowance grants, and allowance-
    ///      holder calls so all three enforce identical checks.
    function _consumeAttestation(
        address account,
        AccountConfig memory cfg,
        address token,
        address payee,
        uint256 amount
    ) internal returns (bytes32 digest) {
        PendingAttestation storage pending = pendingOf[account][token][payee];
        if (!pending.exists) revert NoValidAttestation(account, token, payee);
        if (block.timestamp >= pending.expiry) revert AttestationExpired(pending.expiry, block.timestamp);
        if (amount > pending.amountCap) revert OverAttestationCap(amount, pending.amountCap);

        // Optional Settlement Hub check, aligned to the Hub's real
        // interface (isTaskVerified(bytes32,bytes32) only).
        // TRUST MODEL, stated exactly: the Hub records the SIGIL commitment
        // at settlement time, submitted by the facilitator after OFF-CHAIN
        // verification. isTaskVerified proves a commitment was RECORDED for
        // this task — it does not prove verification happened on-chain,
        // because it didn't. What this hook enforces is: "a recorded
        // commitment exists for this task, and a verifier-signed attestation
        // binds this release to that same task + result commitment."
        if (cfg.settlementHub != address(0)) {
            bool recorded = ISincorSettlementHub(cfg.settlementHub)
                .isTaskVerified(pending.taskSpecHash, pending.resultCommitment);
            if (!recorded) revert CommitmentNotRecorded(pending.taskSpecHash);
        }

        digest = pending.digest;
        delete pendingOf[account][token][payee];
        _removePendingKey(account, token, payee);
        emit AttestationConsumed(account, digest, payee, token, amount);
    }

    /// @dev W-148: gate an allowance-granting call (target = the token).
    ///      approve / permit SET the allowance; increaseAllowance GROWS it;
    ///      the gated amount is the RESULTING total allowance, so repeated
    ///      sub-threshold increases cannot accumulate ungated exposure.
    ///      The spender occupies the attestation's payee slot: a verifier
    ///      attestation for (token, spender) authorizes exposing up to
    ///      amountCap of token to that spender. decreaseAllowance only ever
    ///      shrinks exposure and is never gated. A permit whose owner is
    ///      not the account grants an allowance over someone ELSE's tokens
    ///      and is not a release of this account's funds.
    function _preCheckGrant(
        address account,
        AccountConfig memory cfg,
        address token,
        bytes4 selector,
        bytes calldata inner
    ) internal returns (bytes memory) {
        address spender;
        uint256 newAllowance;
        bool isGrant = true;

        if (selector == ERC20_APPROVE) {
            if (inner.length < 68) revert MalformedExecution();
            spender = address(bytes20(inner[16:36]));
            newAllowance = uint256(bytes32(inner[36:68]));
        } else if (selector == ERC20_INCREASE_ALLOWANCE) {
            if (inner.length < 68) revert MalformedExecution();
            spender = address(bytes20(inner[16:36]));
            newAllowance = allowanceOf[account][token][spender] + uint256(bytes32(inner[36:68]));
        } else if (selector == ERC20_DECREASE_ALLOWANCE) {
            if (inner.length < 68) revert MalformedExecution();
            spender = address(bytes20(inner[16:36]));
            uint256 current = allowanceOf[account][token][spender];
            uint256 delta = uint256(bytes32(inner[36:68]));
            newAllowance = current > delta ? current - delta : 0;
            isGrant = false;
        } else {
            // ERC2612_PERMIT: (owner, spender, value, deadline, v, r, s)
            if (inner.length < 228) revert MalformedExecution();
            address owner = address(bytes20(inner[16:36]));
            if (owner != account) return bytes("");
            spender = address(bytes20(inner[48:68]));
            newAllowance = uint256(bytes32(inner[68:100]));
        }

        bytes32 digest;
        bool consumed = false;
        if (isGrant && newAllowance > cfg.unverifiedThreshold) {
            digest = _consumeAttestation(account, cfg, token, spender, newAllowance);
            consumed = true;
        }
        _setAllowance(account, token, spender, newAllowance);
        return consumed ? abi.encode(digest) : bytes("");
    }

    /// @dev W-148: gate a call into a contract that holds a tracked
    ///      allowance from the account. The holder can pull up to the
    ///      outstanding allowance of EACH token inside this one call, so
    ///      every token whose outstanding allowance exceeds the unverified
    ///      threshold needs its own attestation (payee = the holder),
    ///      consumed here. Allowances at/below the threshold stay
    ///      unverified by design, exactly like direct releases.
    function _gateAllowanceHolderCall(address account, AccountConfig memory cfg, address holder)
        internal
        returns (bytes memory)
    {
        address[] storage tokens = _holderTokensOf[account][holder];
        bytes32 lastDigest;
        bool consumedAny = false;
        for (uint256 i = 0; i < tokens.length; i++) {
            uint256 exposure = allowanceOf[account][tokens[i]][holder];
            if (exposure <= cfg.unverifiedThreshold) continue;
            lastDigest = _consumeAttestation(account, cfg, tokens[i], holder, exposure);
            consumedAny = true;
        }
        return consumedAny ? abi.encode(lastDigest) : bytes("");
    }

    /// @dev Record an allowance granted through a hook-gated execution and
    ///      maintain the holder indexes used by _gateAllowanceHolderCall
    ///      and onUninstall. Recorded in preCheck: if the execution later
    ///      fails, the record overstates the real allowance — the safe
    ///      direction (more calls gated, never fewer).
    function _setAllowance(address account, address token, address spender, uint256 newAllowance) internal {
        uint256 old = allowanceOf[account][token][spender];
        if (old == newAllowance) return;
        allowanceOf[account][token][spender] = newAllowance;

        if (old == 0 && newAllowance > 0) {
            if (_holderTokenIndex[account][spender][token] == 0) {
                _holderTokensOf[account][spender].push(token);
                _holderTokenIndex[account][spender][token] = _holderTokensOf[account][spender].length;
            }
            if (!isAllowanceHolder[account][spender]) {
                isAllowanceHolder[account][spender] = true;
                if (_holderSpenderIndex[account][spender] == 0) {
                    _holderSpendersOf[account].push(spender);
                    _holderSpenderIndex[account][spender] = _holderSpendersOf[account].length;
                }
            }
        } else if (newAllowance == 0) {
            _removeHolderToken(account, spender, token);
            if (_holderTokensOf[account][spender].length == 0 && isAllowanceHolder[account][spender]) {
                isAllowanceHolder[account][spender] = false;
                _removeSpender(account, spender);
            }
        }
    }

    function _removeHolderToken(address account, address spender, address token) internal {
        uint256 idxPlus = _holderTokenIndex[account][spender][token];
        if (idxPlus == 0) return;
        uint256 idx = idxPlus - 1;
        address[] storage tokens = _holderTokensOf[account][spender];
        uint256 last = tokens.length - 1;
        if (idx != last) {
            address moved = tokens[last];
            tokens[idx] = moved;
            _holderTokenIndex[account][spender][moved] = idx + 1;
        }
        tokens.pop();
        delete _holderTokenIndex[account][spender][token];
    }

    function _removeSpender(address account, address spender) internal {
        uint256 idxPlus = _holderSpenderIndex[account][spender];
        if (idxPlus == 0) return;
        uint256 idx = idxPlus - 1;
        address[] storage spenders = _holderSpendersOf[account];
        uint256 last = spenders.length - 1;
        if (idx != last) {
            address moved = spenders[last];
            spenders[idx] = moved;
            _holderSpenderIndex[account][moved] = idx + 1;
        }
        spenders.pop();
        delete _holderSpenderIndex[account][spender];
    }

    /// @notice Tokens on which `spender` currently holds a tracked
    ///         allowance > 0 from `account` (W-148 tracking).
    function holderTokensOf(address account, address spender) external view returns (address[] memory) {
        return _holderTokensOf[account][spender];
    }

    // ------------------------------------------------------------------
    // W-149 pending-key index maintenance
    // ------------------------------------------------------------------

    function _pendingKeyHash(address token, address payee) internal pure returns (bytes32) {
        return keccak256(abi.encode(token, payee));
    }

    function _addPendingKey(address account, address token, address payee) internal {
        bytes32 h = _pendingKeyHash(token, payee);
        if (_pendingKeyIndex[account][h] != 0) return;
        _pendingKeysOf[account].push(PendingKey({token: token, payee: payee}));
        _pendingKeyIndex[account][h] = _pendingKeysOf[account].length;
    }

    function _removePendingKey(address account, address token, address payee) internal {
        bytes32 h = _pendingKeyHash(token, payee);
        uint256 idxPlus = _pendingKeyIndex[account][h];
        if (idxPlus == 0) return;
        uint256 idx = idxPlus - 1;
        PendingKey[] storage keys = _pendingKeysOf[account];
        uint256 last = keys.length - 1;
        if (idx != last) {
            PendingKey memory moved = keys[last];
            keys[idx] = moved;
            _pendingKeyIndex[account][_pendingKeyHash(moved.token, moved.payee)] = idx + 1;
        }
        keys.pop();
        delete _pendingKeyIndex[account][h];
    }

    /// @notice No post-execution state to settle: consumption in preCheck is
    ///         the settlement gate. Kept as a no-op per the IHook interface.
    function postCheck(bytes calldata) external override {}

    /// @dev Decode an ERC-7579 packed single call into a fund-release tuple.
    function _decodeRelease(address account, bytes calldata msgData)
        internal
        pure
        returns (address token, address payee, uint256 amount, bool isRelease)
    {
        // Packed single call: target(20) ++ value(32) ++ innerCallData(rest)
        if (msgData.length < 52) revert MalformedExecution();
        address target = address(bytes20(msgData[0:20]));
        uint256 value = uint256(bytes32(msgData[20:52]));
        bytes calldata inner = msgData[52:];

        if (value > 0) {
            return (address(0), target, value, true);
        }
        if (inner.length >= 4) {
            bytes4 selector = bytes4(inner[0:4]);
            if (selector == ERC20_TRANSFER && inner.length >= 68) {
                payee = address(bytes20(inner[16:36]));
                amount = uint256(bytes32(inner[36:68]));
                return (target, payee, amount, amount > 0);
            }
            if (selector == ERC20_TRANSFER_FROM && inner.length >= 100) {
                address from = address(bytes20(inner[16:36]));
                payee = address(bytes20(inner[48:68]));
                amount = uint256(bytes32(inner[68:100]));
                // Only transfers OUT of the gated account are releases.
                return (target, payee, amount, amount > 0 && from == account);
            }
        }
        return (address(0), address(0), 0, false);
    }
}
