// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

contract SincGovernanceController is AccessControl {
    bytes32 public constant PROPOSER_ROLE = keccak256("PROPOSER_ROLE");
    bytes32 public constant EXECUTOR_ROLE = keccak256("EXECUTOR_ROLE");
    bytes32 public constant CANCELLER_ROLE = keccak256("CANCELLER_ROLE");

    uint256 public minDelay;

    mapping(bytes32 operationId => uint256 eta) public operationEta;
    mapping(bytes32 operationId => bool executed) public operationExecuted;

    event OperationQueued(bytes32 indexed operationId, address indexed target, uint256 value, uint256 eta);
    event OperationExecuted(bytes32 indexed operationId, address indexed target, uint256 value);
    event OperationCancelled(bytes32 indexed operationId);
    event MinDelayUpdated(uint256 oldDelay, uint256 newDelay);

    error InvalidConfig();
    error InvalidEta();
    error OperationMissing();
    error OperationNotReady();
    error OperationAlreadyExecuted();

    constructor(address admin, uint256 minDelaySeconds) {
        if (admin == address(0)) revert InvalidConfig();
        minDelay = minDelaySeconds;

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(PROPOSER_ROLE, admin);
        _grantRole(EXECUTOR_ROLE, admin);
        _grantRole(CANCELLER_ROLE, admin);
    }

    function hashOperation(address target, uint256 value, bytes calldata data, bytes32 salt)
        public
        pure
        returns (bytes32)
    {
        return keccak256(abi.encode(target, value, data, salt));
    }

    function queueOperation(address target, uint256 value, bytes calldata data, bytes32 salt, uint256 eta)
        external
        onlyRole(PROPOSER_ROLE)
        returns (bytes32 operationId)
    {
        if (eta < block.timestamp + minDelay) revert InvalidEta();
        operationId = hashOperation(target, value, data, salt);
        operationEta[operationId] = eta;
        emit OperationQueued(operationId, target, value, eta);
    }

    function executeOperation(address target, uint256 value, bytes calldata data, bytes32 salt)
        external
        payable
        onlyRole(EXECUTOR_ROLE)
        returns (bytes memory result)
    {
        bytes32 operationId = hashOperation(target, value, data, salt);
        uint256 eta = operationEta[operationId];
        if (eta == 0) revert OperationMissing();
        if (operationExecuted[operationId]) revert OperationAlreadyExecuted();
        if (block.timestamp < eta) revert OperationNotReady();

        operationExecuted[operationId] = true;
        (bool ok, bytes memory ret) = target.call{value: value}(data);
        require(ok, "SINCOR_GOV_CALL_FAIL");
        emit OperationExecuted(operationId, target, value);
        return ret;
    }

    function cancelOperation(bytes32 operationId) external onlyRole(CANCELLER_ROLE) {
        if (operationEta[operationId] == 0) revert OperationMissing();
        delete operationEta[operationId];
        delete operationExecuted[operationId];
        emit OperationCancelled(operationId);
    }

    function updateMinDelay(uint256 newDelay) external onlyRole(DEFAULT_ADMIN_ROLE) {
        uint256 old = minDelay;
        minDelay = newDelay;
        emit MinDelayUpdated(old, newDelay);
    }
}
