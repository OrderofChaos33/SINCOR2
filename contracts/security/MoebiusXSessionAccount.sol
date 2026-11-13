// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @title MoebiusXSessionAccount (PROTOTYPE — testnet/sim only)
/// @notice Policy-enforcing smart-account prototype for the MoebiusX trading
///         program. It exists to prove, in fork-sim, the four properties the
///         production design (ZeroDev Kernel + ERC-7579 permission validator,
///         see the MoebiusX session-key design doc) must guarantee:
///           1. a session key CAN execute an allowlisted action within cap;
///           2. it CANNOT exceed a CUMULATIVE spend cap (the gap in the
///              existing SincorSessionValidator, whose maxExecutionValue is
///              per-call only and which has no revocation path);
///           3. it CANNOT call a non-allowlisted target or selector;
///           4. the owner CAN revoke, instantly and unconditionally, and
///              retains full control at all times.
/// @dev    Cumulative-cap enforcement is by ACTUAL token-outflow accounting:
///         the account measures its spend-token balance before and after the
///         session call and charges the delta. A revert unwinds the call, so
///         an over-cap call can never partially settle. Session calls carry
///         zero native value. This contract is a policy-engine prototype, NOT
///         a production account: production uses audited Kernel contracts.
contract MoebiusXSessionAccount {
    address public immutable owner;
    IERC20 public immutable spendToken;

    struct SessionPolicy {
        address sessionKey;
        uint48 validAfter;
        uint48 validUntil;
        uint256 spendCap; // cumulative cap, in spendToken units
        uint256 spent; // cumulative actual outflow charged to this session
        uint256 maxPerCall; // per-call outflow cap, in spendToken units
        bool revoked;
        bool exists;
    }

    mapping(bytes32 sessionId => SessionPolicy) public sessions;
    /// @notice allowlist: sessionId => target => selector => allowed
    mapping(bytes32 sessionId => mapping(address target => mapping(bytes4 selector => bool))) public allowedCall;

    event SessionGranted(bytes32 indexed sessionId, address indexed sessionKey, uint48 validAfter, uint48 validUntil, uint256 spendCap, uint256 maxPerCall);
    event CallAllowed(bytes32 indexed sessionId, address indexed target, bytes4 indexed selector);
    event SessionExecuted(bytes32 indexed sessionId, address indexed target, bytes4 selector, uint256 tokenOutflow, uint256 cumulativeSpent);
    event SessionRevoked(bytes32 indexed sessionId);
    event OwnerExecuted(address indexed target, uint256 value);

    error NotOwner();
    error NotSessionKey();
    error SessionMissing();
    error SessionRevokedErr();
    error SessionNotYetValid();
    error SessionExpired();
    error CallNotAllowlisted();
    error NativeValueForbidden();
    error PerCallCapExceeded();
    error CumulativeCapExceeded();
    error CallFailed(bytes returnData);

    bool private _locked;

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    /// @dev The cumulative charge lands after the external call, so both
    ///      execution entry points are locked against reentry mid-call.
    modifier nonReentrant() {
        require(!_locked, "reentrant");
        _locked = true;
        _;
        _locked = false;
    }

    constructor(address owner_, IERC20 spendToken_) {
        require(owner_ != address(0) && address(spendToken_) != address(0), "zero addr");
        owner = owner_;
        spendToken = spendToken_;
    }

    receive() external payable {}

    /// @notice Owner grants a session. Only the owner can grant; the grant is
    ///         bounded in time, per-call outflow, and cumulative outflow.
    function grantSession(
        bytes32 sessionId,
        address sessionKey,
        uint48 validAfter,
        uint48 validUntil,
        uint256 spendCap,
        uint256 maxPerCall
    ) external onlyOwner {
        require(sessionKey != address(0) && validUntil > validAfter && spendCap > 0 && maxPerCall > 0, "bad policy");
        sessions[sessionId] = SessionPolicy({
            sessionKey: sessionKey,
            validAfter: validAfter,
            validUntil: validUntil,
            spendCap: spendCap,
            spent: 0,
            maxPerCall: maxPerCall,
            revoked: false,
            exists: true
        });
        emit SessionGranted(sessionId, sessionKey, validAfter, validUntil, spendCap, maxPerCall);
    }

    /// @notice Owner allowlists one (target, selector) pair for a session.
    function allowCall(bytes32 sessionId, address target, bytes4 selector) external onlyOwner {
        if (!sessions[sessionId].exists) revert SessionMissing();
        // The spend token and this account are never session-callable targets:
        // that is what prevents approve-drain and self-call policy bypasses.
        require(target != address(spendToken) && target != address(this), "forbidden target");
        allowedCall[sessionId][target][selector] = true;
        emit CallAllowed(sessionId, target, selector);
    }

    /// @notice Owner revocation: immediate, permanent for this session id,
    ///         and requires nothing from the session key.
    function revokeSession(bytes32 sessionId) external onlyOwner {
        SessionPolicy storage p = sessions[sessionId];
        if (!p.exists) revert SessionMissing();
        p.revoked = true;
        emit SessionRevoked(sessionId);
    }

    /// @notice The session key executes one allowlisted call. Enforcement
    ///         order: identity -> existence -> revocation -> time window ->
    ///         allowlist -> zero native value -> call -> actual-outflow
    ///         accounting against per-call and cumulative caps (any breach
    ///         reverts the entire call atomically).
    function executeAsSession(bytes32 sessionId, address target, bytes calldata data) external nonReentrant returns (bytes memory ret) {
        SessionPolicy storage p = sessions[sessionId];
        if (!p.exists) revert SessionMissing();
        if (msg.sender != p.sessionKey) revert NotSessionKey();
        if (p.revoked) revert SessionRevokedErr();
        if (block.timestamp < p.validAfter) revert SessionNotYetValid();
        if (block.timestamp > p.validUntil) revert SessionExpired();
        if (data.length < 4) revert CallNotAllowlisted();
        bytes4 selector = bytes4(data[:4]);
        if (!allowedCall[sessionId][target][selector]) revert CallNotAllowlisted();

        uint256 balanceBefore = spendToken.balanceOf(address(this));
        (bool ok, bytes memory returnData) = target.call(data);
        if (!ok) revert CallFailed(returnData);
        uint256 balanceAfter = spendToken.balanceOf(address(this));

        uint256 outflow = balanceBefore > balanceAfter ? balanceBefore - balanceAfter : 0;
        if (outflow > p.maxPerCall) revert PerCallCapExceeded();
        if (p.spent + outflow > p.spendCap) revert CumulativeCapExceeded();
        p.spent += outflow;

        emit SessionExecuted(sessionId, target, selector, outflow, p.spent);
        return returnData;
    }

    /// @notice Owner execution is unrestricted — the owner never needs the
    ///         session's permission and can always drain or redirect funds.
    function executeAsOwner(address target, uint256 value, bytes calldata data) external onlyOwner nonReentrant returns (bytes memory ret) {
        (bool ok, bytes memory returnData) = target.call{value: value}(data);
        if (!ok) revert CallFailed(returnData);
        emit OwnerExecuted(target, value);
        return returnData;
    }
}
