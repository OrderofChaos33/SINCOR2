// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20 {
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

contract SincorSettlementHub {
    IERC20 public immutable usdc;
    address public feeTreasury;
    uint256 public protocolFeeBps = 500; // 5.00% (500 bps) — locked SINCOR fee canon

    bytes32 public constant DOMAIN_TYPEHASH =
        keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)");

    bytes32 public constant SEAL_TYPEHASH =
        keccak256(
            "SealAuthorization(bytes32 taskId,address buyer,address provider,uint256 amount,bytes32 expectedSchemaHash,uint256 deadline,uint256 nonce)"
        );

    bytes32 public immutable DOMAIN_SEPARATOR;

    mapping(address => uint256) public nonces;
    mapping(bytes32 => bytes32) public verifiedTaskSigils;

    struct SealAuthorization {
        bytes32 taskId;
        address buyer;
        address provider;
        uint256 amount;
        bytes32 expectedSchemaHash;
        uint256 deadline;
        uint256 nonce;
    }

    error SealExpired();
    error InvalidNonce();
    error InvalidSealSignature();
    error SigilCommitmentZero();
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

    function verifyAndSettleSeal(
        SealAuthorization calldata seal,
        bytes32 sigilCommitment,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external returns (bool) {
        if (block.timestamp > seal.deadline) revert SealExpired();
        if (sigilCommitment == bytes32(0)) revert SigilCommitmentZero();
        if (seal.nonce != nonces[seal.buyer]++) revert InvalidNonce();

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

        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR, structHash));
        address recovered = ecrecover(digest, v, r, s);
        if (recovered == address(0) || recovered != seal.buyer) revert InvalidSealSignature();

        verifiedTaskSigils[seal.taskId] = sigilCommitment;

        uint256 fee = (seal.amount * protocolFeeBps) / 10000;
        uint256 payout = seal.amount - fee;

        if (fee > 0) {
            if (!usdc.transferFrom(seal.buyer, feeTreasury, fee)) revert UsdcTransferFailed();
        }
        if (!usdc.transferFrom(seal.buyer, seal.provider, payout)) revert UsdcTransferFailed();

        emit SealSettled(seal.taskId, seal.buyer, seal.provider, payout, fee, sigilCommitment);
        return true;
    }
}
