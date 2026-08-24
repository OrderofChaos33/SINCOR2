// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import {SincorSessionValidator} from "../contracts/security/SincorSessionValidator.sol";

contract SincorSessionValidatorTest is Test {
    using MessageHashUtils for bytes32;

    SincorSessionValidator public validator;

    address public account = address(0xA11CE);
    uint256 public sessionPk = 0xBEEF;
    address public sessionKey;
    address public target = address(0xCAFE);
    bytes4 public selector = bytes4(keccak256("execute(address,uint256,bytes)"));

    bytes4 constant EIP1271_SUCCESS = 0x1626ba7e;
    bytes4 constant EIP1271_FAILED = 0xffffffff;

    function setUp() public {
        validator = new SincorSessionValidator();
        sessionKey = vm.addr(sessionPk);
    }

    function _register(uint48 validAfter, uint48 validUntil, uint256 maxValue) internal returns (bytes32 sessionHash) {
        vm.prank(account);
        sessionHash = validator.registerSession(sessionKey, validUntil, validAfter, target, selector, maxValue);
    }

    function _sign(bytes32 hash) internal view returns (bytes memory sig) {
        bytes32 digest = hash.toEthSignedMessageHash();
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(sessionPk, digest);
        sig = abi.encodePacked(r, s, v);
    }

    function test_IsModuleTypeValidator() public view {
        assertTrue(validator.isModuleType(1));
        assertFalse(validator.isModuleType(2));
    }

    function test_RegisterSessionStoresConfig() public {
        bytes32 sessionHash = _register(0, uint48(block.timestamp + 1 days), 1 ether);

        (
            address key,
            uint48 validUntil,
            uint48 validAfter,
            address allowedTarget,
            bytes4 allowedSelector,
            uint256 maxValue
        ) = validator.accountSessions(account, sessionHash);

        assertEq(key, sessionKey);
        assertEq(allowedTarget, target);
        assertEq(allowedSelector, selector);
        assertEq(maxValue, 1 ether);
        assertEq(validAfter, 0);
        assertGt(validUntil, uint48(block.timestamp));
    }

    function test_ValidSignatureWithSender() public {
        bytes32 sessionHash = _register(0, uint48(block.timestamp + 1 days), 1 ether);
        bytes32 hash = keccak256("sincor-session");
        bytes memory packed = abi.encode(sessionHash, _sign(hash));

        bytes4 result = validator.isValidSignatureWithSender(account, hash, packed);
        assertEq(result, EIP1271_SUCCESS);
    }

    function test_InvalidSignatureFails() public {
        bytes32 sessionHash = _register(0, uint48(block.timestamp + 1 days), 1 ether);
        bytes32 hash = keccak256("sincor-session");

        uint256 otherPk = 0xD00D;
        bytes32 digest = hash.toEthSignedMessageHash();
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(otherPk, digest);
        bytes memory packed = abi.encode(sessionHash, abi.encodePacked(r, s, v));

        bytes4 result = validator.isValidSignatureWithSender(account, hash, packed);
        assertEq(result, EIP1271_FAILED);
    }

    function test_ExpiredSessionFails1271() public {
        bytes32 sessionHash = _register(0, uint48(block.timestamp + 1 hours), 1 ether);
        vm.warp(block.timestamp + 2 hours);

        bytes32 hash = keccak256("sincor-session");
        bytes memory packed = abi.encode(sessionHash, _sign(hash));
        bytes4 result = validator.isValidSignatureWithSender(account, hash, packed);
        assertEq(result, EIP1271_FAILED);
    }

    function test_ValidateUserOpSuccess() public {
        bytes32 sessionHash = _register(0, uint48(block.timestamp + 1 days), 1 ether);
        bytes32 userOpHash = keccak256("user-op");
        bytes memory userOp = abi.encode(account, sessionHash, _sign(userOpHash), target, 0.1 ether, selector);

        uint256 validationData = validator.validateUserOp(userOp, userOpHash);
        assertNotEq(validationData, 1);
    }

    function test_ValidateUserOpRejectsWrongTarget() public {
        bytes32 sessionHash = _register(0, uint48(block.timestamp + 1 days), 1 ether);
        bytes32 userOpHash = keccak256("user-op");
        bytes memory userOp = abi.encode(account, sessionHash, _sign(userOpHash), address(0xDEAD), 0.1 ether, selector);

        uint256 validationData = validator.validateUserOp(userOp, userOpHash);
        assertEq(validationData, 1);
    }

    function test_ValidateUserOpRejectsOverMaxValue() public {
        bytes32 sessionHash = _register(0, uint48(block.timestamp + 1 days), 1 ether);
        bytes32 userOpHash = keccak256("user-op");
        bytes memory userOp = abi.encode(account, sessionHash, _sign(userOpHash), target, 2 ether, selector);

        uint256 validationData = validator.validateUserOp(userOp, userOpHash);
        assertEq(validationData, 1);
    }

    function test_ValidateUserOpRejectsUnknownSession() public {
        bytes32 userOpHash = keccak256("user-op");
        bytes memory userOp = abi.encode(account, bytes32(uint256(1)), _sign(userOpHash), target, 0, selector);
        uint256 validationData = validator.validateUserOp(userOp, userOpHash);
        assertEq(validationData, 1);
    }
}
