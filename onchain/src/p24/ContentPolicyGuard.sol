// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title P24 ContentPolicyGuard — no_price_talk enforcement at issuance
/// @notice The real screening (phrase-based no_price_talk deny-list) runs
///         offchain in the onboarding agent before any onchain action; the
///         guard records the ruleset version per token and blocks issuance
///         unless the token's metadata passed screening. The ruleset version
///         is emitted in events for auditability.
/// @dev Self-contained for bare solc 0.8.24 compilation.
contract ContentPolicyGuard {
    string public constant RULESET_VERSION = "1.0.0";

    address public admin;
    address public screener; // the onboarding agent's address
    mapping(address => bool) public screened; // token => passed policy screen
    mapping(address => string) public screenedVersion; // token => ruleset version

    event Screened(address indexed token, string version);
    event Flagged(address indexed token, string reason);

    error NotAdmin();
    error NotScreener();
    error NotScreened();

    constructor(address _admin, address _screener) {
        admin = _admin;
        screener = _screener;
    }

    function setScreener(address _screener) external {
        if (msg.sender != admin) revert NotAdmin();
        screener = _screener;
    }

    /// @notice Called by the screener after the offchain phrase-based screen
    ///         passes. Rejections never reach the chain (they are logged
    ///         offchain with rule version + matched phrase, not full text).
    function markScreened(address token) external {
        if (msg.sender != screener) revert NotScreener();
        screened[token] = true;
        screenedVersion[token] = RULESET_VERSION;
        emit Screened(token, RULESET_VERSION);
    }

    function flagForReview(address token, string calldata reason) external {
        if (msg.sender != screener && msg.sender != admin) revert NotScreener();
        screened[token] = false;
        emit Flagged(token, reason);
    }

    /// @notice Issuance paths call this: unscreened tokens cannot be issued.
    function requireScreened(address token) external view {
        if (!screened[token]) revert NotScreened();
    }

    function version() external pure returns (string memory) {
        return RULESET_VERSION;
    }
}
