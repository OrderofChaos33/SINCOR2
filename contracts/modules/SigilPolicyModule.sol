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

    function onUninstall(bytes calldata) external override {
        address account = msg.sender;
        if (!configOf[account].initialized) revert NotInstalled(account);
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

        emit AttestationRegistered(att.account, digest, att.payee, att.token, att.amountCap, att.expiry, att.nonce);
    }

    /// @notice The account may revoke its own outstanding attestation.
    function revokeAttestation(address token, address payee) external {
        address account = msg.sender;
        PendingAttestation storage pending = pendingOf[account][token][payee];
        if (!pending.exists) revert NoValidAttestation(account, token, payee);
        bytes32 digest = pending.digest;
        delete pendingOf[account][token][payee];
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
    ///         fund release iff value > 0 (native) or innerCallData is an
    ///         ERC20 transfer / transferFrom out of the account. Non-release
    ///         calls are not gated and return empty hookData. A release is
    ///         allowed only against a pending, unexpired attestation whose
    ///         (token, payee) match and whose amountCap covers the amount;
    ///         the attestation is consumed here (single-use), so a replayed
    ///         execution finds nothing and reverts.
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

        (address token, address payee, uint256 amount, bool isRelease) = _decodeRelease(account, msgData);
        if (!isRelease) return bytes("");

        // Unverified threshold, applied to the DECODED amount. For an ERC20
        // transfer/transferFrom execution the ETH msgValue is 0, so any
        // threshold keyed on msgValue (as in the founder spec-drop draft
        // hook) would let arbitrarily large token settlements through
        // ungated. Here `amount` is the token amount decoded from the
        // execution calldata (raw units of the moved asset).
        if (amount <= cfg.unverifiedThreshold) return bytes("");

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

        bytes32 digest = pending.digest;
        delete pendingOf[account][token][payee];
        emit AttestationConsumed(account, digest, payee, token, amount);
        return abi.encode(digest);
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
