// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title ScopedPausable
/// @notice Guardian-gated emergency pause with an immutable onchain sunset.
///
/// Scope discipline (ratified 2026-09-27):
/// - `whenNotPaused` applies ONLY to entrypoints that inject new state or
///   capital: auction creation, bid commits, stake deposits.
/// - Settlement, withdrawal, dispute, timeout and adjudication paths are
///   NEVER pausable. A pause must freeze new activity, never trap user
///   funds or block in-flight work from settling.
///
/// Sunset (EIP-6780 compatible): post-Cancun SELFDESTRUCT no longer erases
/// code, so the sunset is a hard block-height cap. After SUNSET_BLOCK,
/// `setPause` reverts and `whenNotPaused` stops enforcing — the protocol
/// becomes unpausable by construction. The audit must land inside the
/// sunset window.
///
/// The guardian is immutable and constructor-injected: the deployment
/// script must pass a real 2-of-3 multisig address. Nothing is hardcoded.
abstract contract ScopedPausable {
    bool public paused;
    address public immutable guardianMultisig;
    uint256 public immutable SUNSET_BLOCK;

    /// @dev Base Denim: 200ms per block -> 180 days = 77,760,000 blocks.
    uint256 public constant SUNSET_DURATION_BLOCKS = 77_760_000;

    event ProtocolPaused(address indexed caller);
    event ProtocolUnpaused(address indexed caller);

    error PauseExpired();
    error EmergencyPaused();
    error UnauthorizedGuardian();

    constructor(address _guardian) {
        require(_guardian != address(0), "Invalid guardian");
        guardianMultisig = _guardian;
        SUNSET_BLOCK = block.number + SUNSET_DURATION_BLOCKS;
    }

    /// @dev Enforces the pause only before the sunset height. After the
    ///      sunset the protocol is unpausable even if `paused` is stale.
    modifier whenNotPaused() {
        if (block.number < SUNSET_BLOCK && paused) {
            revert EmergencyPaused();
        }
        _;
    }

    /// @dev Guardian-only. Permanently disabled at/after SUNSET_BLOCK.
    ///      Offchain monitoring pages human signers; no automated writer
    ///      may ever call this.
    function setPause(bool _state) external {
        if (msg.sender != guardianMultisig) revert UnauthorizedGuardian();
        if (block.number >= SUNSET_BLOCK) revert PauseExpired();
        paused = _state;
        if (_state) emit ProtocolPaused(msg.sender);
        else emit ProtocolUnpaused(msg.sender);
    }
}

// ---------------------------------------------------------------------------
// ReentrancyGuard (vendored from OpenZeppelin Contracts v5, MIT-licensed).
//
// Co-located in this file deliberately: the Python solcx harnesses compile
// ExecutionEscrowManager from a fixed source set (this file included) with
// no remappings and no lib/ paths, so a separate guard file or an
// `@openzeppelin/...` import is unresolvable there. Semantics are the
// upstream design: a 1/2 status word, ReentrancyGuardReentrantCall on
// reentry, and guard state shared across every guarded function of the
// inheriting contract (cross-function reentry included).
// ---------------------------------------------------------------------------

/// @title ReentrancyGuard
/// @notice Prevents reentrant calls to functions carrying `nonReentrant`.
abstract contract ReentrancyGuard {
    uint256 private constant NOT_ENTERED = 1;
    uint256 private constant ENTERED = 2;

    uint256 private _status;

    error ReentrancyGuardReentrantCall();

    constructor() {
        _status = NOT_ENTERED;
    }

    /// @dev Prevents a contract from calling itself, directly or indirectly.
    ///      Any `nonReentrant` function may not be called while another one
    ///      is in progress, including indirectly via an external call out
    ///      and back in.
    modifier nonReentrant() {
        _nonReentrantBefore();
        _;
        _nonReentrantAfter();
    }

    function _nonReentrantBefore() private {
        if (_status == ENTERED) revert ReentrancyGuardReentrantCall();
        _status = ENTERED;
    }

    function _nonReentrantAfter() private {
        _status = NOT_ENTERED;
    }

    /// @dev Returns true if any guarded function is currently executing.
    function _reentrancyGuardEntered() internal view returns (bool) {
        return _status == ENTERED;
    }
}
