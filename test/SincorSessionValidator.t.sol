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

    // ── Fuzz: caps / targets / selectors / signers / time windows ─────

    function _signWith(uint256 pk, bytes32 hash) internal view returns (bytes memory sig) {
        bytes32 digest = hash.toEthSignedMessageHash();
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, digest);
        sig = abi.encodePacked(r, s, v);
    }

    function testFuzz_ValidateUserOpRejectsValueAboveCap(uint256 maxValue, uint256 overBy) public {
        maxValue = bound(maxValue, 0, 100 ether);
        uint256 value = maxValue + bound(overBy, 1, 100 ether);
        bytes32 sessionHash = _register(0, uint48(block.timestamp + 1 days), maxValue);
        bytes32 userOpHash = keccak256("user-op");
        bytes memory userOp = abi.encode(account, sessionHash, _sign(userOpHash), target, value, selector);
        assertEq(validator.validateUserOp(userOp, userOpHash), 1);
    }

    function testFuzz_ValidateUserOpAcceptsValueAtOrBelowCap(uint256 maxValue, uint256 value) public {
        maxValue = bound(maxValue, 0, 100 ether);
        value = bound(value, 0, maxValue);
        uint48 validUntil = uint48(block.timestamp + 1 days);
        bytes32 sessionHash = _register(0, validUntil, maxValue);
        bytes32 userOpHash = keccak256("user-op");
        bytes memory userOp = abi.encode(account, sessionHash, _sign(userOpHash), target, value, selector);
        uint256 vd = validator.validateUserOp(userOp, userOpHash);
        // Success packs the session window: validUntil << 160 | validAfter << 208.
        assertEq(vd, uint256(validUntil) << 160);
    }

    function testFuzz_ValidateUserOpRejectsAnyWrongTarget(address badTarget) public {
        vm.assume(badTarget != target);
        bytes32 sessionHash = _register(0, uint48(block.timestamp + 1 days), 1 ether);
        bytes32 userOpHash = keccak256("user-op");
        bytes memory userOp = abi.encode(account, sessionHash, _sign(userOpHash), badTarget, 0, selector);
        assertEq(validator.validateUserOp(userOp, userOpHash), 1);
    }

    function testFuzz_ValidateUserOpRejectsAnyWrongSelector(bytes4 badSelector) public {
        vm.assume(badSelector != selector);
        bytes32 sessionHash = _register(0, uint48(block.timestamp + 1 days), 1 ether);
        bytes32 userOpHash = keccak256("user-op");
        bytes memory userOp = abi.encode(account, sessionHash, _sign(userOpHash), target, 0, badSelector);
        assertEq(validator.validateUserOp(userOp, userOpHash), 1);
    }

    function testFuzz_WrongSignerAlwaysRejected(uint256 wrongSeed, bytes32 userOpHash) public {
        uint256 wrongPk = bound(wrongSeed, 1, 1_000_000);
        vm.assume(vm.addr(wrongPk) != sessionKey);
        bytes32 sessionHash = _register(0, uint48(block.timestamp + 1 days), 1 ether);

        bytes memory userOp = abi.encode(account, sessionHash, _signWith(wrongPk, userOpHash), target, 0, selector);
        assertEq(validator.validateUserOp(userOp, userOpHash), 1);

        bytes memory packed = abi.encode(sessionHash, _signWith(wrongPk, userOpHash));
        assertEq(validator.isValidSignatureWithSender(account, userOpHash, packed), EIP1271_FAILED);
    }

    function testFuzz_UnknownSessionHashAlwaysRejected(bytes32 fakeHash) public {
        bytes32 realHash = _register(0, uint48(block.timestamp + 1 days), 1 ether);
        vm.assume(fakeHash != realHash);
        bytes32 userOpHash = keccak256("user-op");
        bytes memory userOp = abi.encode(account, fakeHash, _sign(userOpHash), target, 0, selector);
        assertEq(validator.validateUserOp(userOp, userOpHash), 1);
    }

    function testFuzz_SignatureWindowEnforced(uint48 afterOff, uint48 lenOff, uint48 checkOff) public {
        uint256 now0 = block.timestamp;
        uint256 validAfter = bound(uint256(afterOff), 0, 1 days);
        uint256 validUntil = validAfter + bound(uint256(lenOff), 1 hours, 30 days);
        uint256 checkAt = bound(uint256(checkOff), 0, 40 days);

        bytes32 sessionHash = _register(uint48(now0 + validAfter), uint48(now0 + validUntil), 1 ether);
        vm.warp(now0 + checkAt);

        bytes32 hash = keccak256("sincor-session");
        bytes memory packed = abi.encode(sessionHash, _sign(hash));
        bytes4 result = validator.isValidSignatureWithSender(account, hash, packed);
        if (checkAt >= validAfter && checkAt <= validUntil) {
            assertEq(result, EIP1271_SUCCESS);
        } else {
            assertEq(result, EIP1271_FAILED);
        }
    }
}
