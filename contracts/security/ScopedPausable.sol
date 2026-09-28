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

    /// @dev Base L2: 2s per block -> 180 days = 7,776,000 blocks.
    uint256 public constant SUNSET_DURATION_BLOCKS = 7_776_000;

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
