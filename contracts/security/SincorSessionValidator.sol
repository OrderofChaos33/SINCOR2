// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";

interface IERC7579Module {
    function onInstall(bytes calldata data) external;
    function onUninstall(bytes calldata data) external;
    function isModuleType(uint256 moduleTypeId) external view returns (bool);
}

interface IERC7579Validator is IERC7579Module {
    function validateUserOp(
        bytes calldata userOp,
        bytes32 userOpHash
    ) external returns (uint256 validationData);

    function isValidSignatureWithSender(
        address sender,
        bytes32 hash,
        bytes calldata signature
    ) external view returns (bytes4);
}

contract SincorSessionValidator is IERC7579Validator {
    using ECDSA for bytes32;
    using MessageHashUtils for bytes32;

    uint256 public constant MODULE_TYPE_VALIDATOR = 1;
    bytes4 internal constant EIP1271_SUCCESS = 0x1626ba7e;
    bytes4 internal constant EIP1271_FAILED = 0xffffffff;

    struct SessionConfig {
        address sessionKey;
        uint48 validUntil;
        uint48 validAfter;
        address allowedTarget;
        bytes4 allowedSelector;
        uint256 maxExecutionValue;
    }

    mapping(address => mapping(bytes32 => SessionConfig)) public accountSessions;

    event SessionRegistered(address indexed account, bytes32 indexed sessionHash, address indexed sessionKey);

    function onInstall(bytes calldata data) external override {}
    function onUninstall(bytes calldata data) external override {}

    function isModuleType(uint256 moduleTypeId) external pure override returns (bool) {
        return moduleTypeId == MODULE_TYPE_VALIDATOR;
    }

    function registerSession(
        address sessionKey,
        uint48 validUntil,
        uint48 validAfter,
        address allowedTarget,
        bytes4 allowedSelector,
        uint256 maxExecutionValue
    ) external returns (bytes32 sessionHash) {
        sessionHash = keccak256(abi.encodePacked(sessionKey, allowedTarget, allowedSelector));
        accountSessions[msg.sender][sessionHash] = SessionConfig({
            sessionKey: sessionKey,
            validUntil: validUntil,
            validAfter: validAfter,
            allowedTarget: allowedTarget,
            allowedSelector: allowedSelector,
            maxExecutionValue: maxExecutionValue
        });

        emit SessionRegistered(msg.sender, sessionHash, sessionKey);
    }

    function validateUserOp(
        bytes calldata userOp,
        bytes32 userOpHash
    ) external view override returns (uint256 validationData) {
        (address account, bytes32 sessionHash, bytes memory signature, address target, uint256 value, bytes4 selector) = 
            abi.decode(userOp, (address, bytes32, bytes, address, uint256, bytes4));

        SessionConfig memory session = accountSessions[account][sessionHash];

        if (session.sessionKey == address(0)) return 1;
        if (session.allowedTarget != address(0) && target != session.allowedTarget) return 1;
        if (session.allowedSelector != bytes4(0) && selector != session.allowedSelector) return 1;
        if (value > session.maxExecutionValue) return 1;

        bytes32 ethHash = userOpHash.toEthSignedMessageHash();
        if (ethHash.recover(signature) != session.sessionKey) {
            return 1;
        }

        return (uint256(session.validUntil) << 160) | (uint256(session.validAfter) << 208);
    }

    function isValidSignatureWithSender(
        address sender,
        bytes32 hash,
        bytes calldata signature
    ) external view override returns (bytes4) {
        (bytes32 sessionHash, bytes memory sig) = abi.decode(signature, (bytes32, bytes));
        SessionConfig memory session = accountSessions[sender][sessionHash];

        if (block.timestamp > session.validUntil || block.timestamp < session.validAfter) {
            return EIP1271_FAILED;
        }

        if (hash.toEthSignedMessageHash().recover(sig) == session.sessionKey) {
            return EIP1271_SUCCESS;
        }
        return EIP1271_FAILED;
    }
}
