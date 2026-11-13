// TEST COPY — vendored for cross-implementation tests ONLY. Do not deploy.
//
// Copied from the founder's spec drop:
//   ~/workspace/goals/agent-infra-partnership-outreach/files/spec-drop-2026-11-11/SincorSettlementHub.sol
// The spec-drop reference file is UNVERIFIED and NOT deployed anywhere; it
// is never edited by this package.
//
// CHANGE vs the spec drop (the only functional change):
//   protocolFeeBps: 100 (1%) -> 500 (5%).
// WHY: the locked SINCOR fee canon is 5% (500 bps), decided 2026-09-26 and
// reconfirmed 2026-11-11. The draft's hardcoded 100 bps contradicts canon
// and is recorded as a known defect in the reference file's header notes.
// Chain tests must exercise the canon fee split (provider 95% / treasury 5%).
//
// Known defects carried over from the draft (intentionally NOT fixed here —
// the tests document the behaviour; the Python Hub engine compensates
// off-chain, see sincor_x402/hub_engine.py):
//   * expectedSchemaHash is signed but never enforced on-chain.
//   * Funds move via ERC-20 transferFrom; buyers must pre-approve the Hub.
//
// RE-VENDORED 2026-11-13 for the W-150 fix (branch xioix/hub-w150-fix):
// this copy now mirrors the FIXED contracts/modules/SincorSettlementHub.sol
// — sigilCommitment is a field of the signed SealAuthorization struct
// (SEAL_TYPEHASH changed), verifyAndSettleSeal no longer takes a separate
// submitter-supplied sigilCommitment, and zero-amount seals revert
// (SealAmountZero). The draft defect "the Hub records whatever
// sigilCommitment the submitter passes" is FIXED in the source contract
// and therefore fixed here; the Python engine's off-chain verification
// remains as a pre-submission layer, no longer as the only binding.
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20 {
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

contract SincorSettlementHub {
    IERC20 public immutable usdc;
    address public feeTreasury;
    uint256 public protocolFeeBps = 500; // 5.00% (500 bps) — fee canon; spec drop had 100 (defect)

    bytes32 public constant DOMAIN_TYPEHASH =
        keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)");

    // SEAL Envelope Struct Typehash (W-150: sigilCommitment is signed)
    bytes32 public constant SEAL_TYPEHASH =
        keccak256(
            "SealAuthorization(bytes32 taskId,address buyer,address provider,uint256 amount,bytes32 expectedSchemaHash,bytes32 sigilCommitment,uint256 deadline,uint256 nonce)"
        );

    bytes32 public immutable DOMAIN_SEPARATOR;

    // Tracks nonces per buyer agent & verified SIGIL commitments per taskId
    mapping(address => uint256) public nonces;
    mapping(bytes32 => bytes32) public verifiedTaskSigils;

    struct SealAuthorization {
        bytes32 taskId;
        address buyer;
        address provider;
        uint256 amount;
        bytes32 expectedSchemaHash;
        bytes32 sigilCommitment;
        uint256 deadline;
        uint256 nonce;
    }

    error SealExpired();
    error InvalidNonce();
    error InvalidSealSignature();
    error SigilCommitmentZero();
    error SealAmountZero();
    error UsdcTransferFailed();

    event SealSettled(
        bytes32 indexed taskId,
        address indexed buyer,
        address indexed provider,
        uint256 netPayout,
        uint256 protocolFee,
        bytes32 sigilCommitment
    );

    constructor(address _usdc, address _feeTreasury) {
        usdc = IERC20(_usdc);
        feeTreasury = _feeTreasury;
        DOMAIN_SEPARATOR = keccak256(
            abi.encode(
                DOMAIN_TYPEHASH,
                keccak256(bytes("SINCOR_SEAL")),
                keccak256(bytes("1")),
                block.chainid,
                address(this)
            )
        );
    }

    function isTaskVerified(bytes32 taskId, bytes32 expectedSigil) external view returns (bool) {
        bytes32 recorded = verifiedTaskSigils[taskId];
        return recorded != bytes32(0) && recorded == expectedSigil;
    }

    /**
     * @notice Verifies the Hiring Agent's EIP-712 SEAL signature, records the Worker Agent's
     *         SIGIL output commitment, routes protocol fee, and settles USDC on Base.
     */
    function verifyAndSettleSeal(
        SealAuthorization calldata seal,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external returns (bool) {
        if (block.timestamp > seal.deadline) revert SealExpired();
        if (seal.sigilCommitment == bytes32(0)) revert SigilCommitmentZero();
        if (seal.amount == 0) revert SealAmountZero();
        if (seal.nonce != nonces[seal.buyer]++) revert InvalidNonce();

        bytes32 structHash = keccak256(
            abi.encode(
                SEAL_TYPEHASH,
                seal.taskId,
                seal.buyer,
                seal.provider,
                seal.amount,
                seal.expectedSchemaHash,
                seal.sigilCommitment,
                seal.deadline,
                seal.nonce
            )
        );

        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR, structHash));
        address recovered = ecrecover(digest, v, r, s);
        if (recovered == address(0) || recovered != seal.buyer) revert InvalidSealSignature();

        // Record SIGIL state commitment on-chain before moving funds (CEI pattern)
        verifiedTaskSigils[seal.taskId] = seal.sigilCommitment;

        uint256 fee = (seal.amount * protocolFeeBps) / 10000;
        uint256 payout = seal.amount - fee;

        if (fee > 0) {
            if (!usdc.transferFrom(seal.buyer, feeTreasury, fee)) revert UsdcTransferFailed();
        }
        if (!usdc.transferFrom(seal.buyer, seal.provider, payout)) revert UsdcTransferFailed();

        emit SealSettled(seal.taskId, seal.buyer, seal.provider, payout, fee, seal.sigilCommitment);
        return true;
    }
}
