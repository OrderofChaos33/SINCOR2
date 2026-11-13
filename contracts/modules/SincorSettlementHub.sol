// Provenance: founder spec drop 2026-11-11 (external draft, UNVERIFIED,
// NOT deployed anywhere, NOT the live SINCOR contract). Copied into this
// worktree as the interface source of truth for SigilPolicyModule's Hub
// integration, with ONE correction: protocolFeeBps 100 -> 500 (locked 5%
// fee canon). Trust model: this Hub RECORDS the SIGIL commitment at
// settlement time, submitted by the facilitator after OFF-CHAIN
// verification; isTaskVerified proves a commitment was recorded, not that
// verification happened on-chain. expectedSchemaHash is signed but never
// enforced on-chain in this draft.
// REFERENCE DRAFT — pasted by founder 2026-11-11 from an external draft.
// STATUS: UNVERIFIED, NOT deployed anywhere, NOT the live SINCOR contract.
// Known discrepancies flagged by main agent 2026-11-11:
//  (1) protocolFeeBps = 100 (1%) contradicts locked fee canon of 5% (500 bps).
//      [Fee corrected to 500 bps in this copy; discrepancy closed.]
//  (2) expectedSchemaHash is signed but never enforced on-chain.
//      [Partially closed by the W-150 fix, 2026-11-13: sigilCommitment is
//      now a field of the signed SEAL struct, so the Hub records exactly
//      the commitment the BUYER signed — a settlement submitter can no
//      longer choose or substitute it (red-team hunt 47, W-150: front-run
//      junk-sigil poisoning). expectedSchemaHash remains signed-but-
//      unenforced; output verification still sits with the off-chain
//      facilitator/engine before submission.]
//  (3) Funds move via ERC-20 transferFrom (buyer must pre-approve the Hub);
//      the SEAL signature alone does not move funds, and nothing is escrowed
//      at authorization time. No CNP/auction logic exists in this contract.
// W-150 fix (2026-11-13, branch xioix/hub-w150-fix): SealAuthorization gains
// a signed `sigilCommitment` field (SEAL_TYPEHASH changed accordingly —
// every SEAL signature issued against the old struct is invalid here), the
// separate submitter-supplied sigilCommitment parameter is removed from
// verifyAndSettleSeal, and zero-amount seals now revert (SealAmountZero)
// instead of recording a "verified" sigil with no payment.
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20 {
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

contract SincorSettlementHub {
    IERC20 public immutable usdc;
    address public feeTreasury;
    uint256 public protocolFeeBps = 500; // 5.00% (500 bps) — CORRECTED to the locked SINCOR fee canon (2026-09-26);
    // the founder spec-drop draft carried 100 bps, which contradicts canon.

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
     * @dev W-150: the recorded SIGIL commitment is `seal.sigilCommitment` —
     *      a field of the buyer-signed struct. There is deliberately no
     *      submitter-supplied commitment parameter: whoever relays the
     *      settlement cannot choose what gets recorded. Zero-amount seals
     *      revert (SealAmountZero) so a recorded "verified" sigil always
     *      corresponds to an actual payment.
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
