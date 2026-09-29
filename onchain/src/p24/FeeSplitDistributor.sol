// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title P24 FeeSplitDistributor — conservation-exact 4940/4940/120 split
/// @notice Every trade pays a 10% fee. Each leg is floored; residual dust goes
///         to the treasury leg. Invariant: creator + platform + treasury == fee
///         for every input (structural). Pull-claims; no push loops.
/// @dev Self-contained for bare solc 0.8.24 compilation. Balances are tracked
///      in USDC-wei; actual token movement is wired at integration.
contract FeeSplitDistributor {
    uint256 public constant BPS = 10_000;
    uint256 public constant CREATOR_BPS = 4940;
    uint256 public constant PLATFORM_BPS = 4940;
    uint256 public constant TREASURY_BPS = 120;
    uint256 public constant CREATOR_FLOOR_BPS = 4000;
    address public constant TREASURY = 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac;

    mapping(address => mapping(address => uint256)) public creatorClaimable; // token => creator => wei
    mapping(address => uint256) public platformClaimable; // token => wei
    mapping(address => uint256) public treasuryClaimable; // token => wei

    event Split(address indexed token, uint256 fee, uint256 creator, uint256 platform, uint256 treasury, uint256 dust);
    event Claimed(address indexed token, address indexed to, uint256 amount, string leg);

    error SplitSumsWrong();

    /// @notice Split a trade fee. Called by the trade settlement path.
    function split(address token, address creator, uint256 fee) external {
        uint256 c = fee * CREATOR_BPS / BPS;
        uint256 p = fee * PLATFORM_BPS / BPS;
        uint256 tBase = fee * TREASURY_BPS / BPS;
        uint256 dust = fee - (c + p + tBase);
        uint256 t = tBase + dust;
        assert(c + p + t == fee); // structural conservation
        creatorClaimable[token][creator] += c;
        platformClaimable[token] += p;
        treasuryClaimable[token] += t;
        emit Split(token, fee, c, p, t, dust);
    }

    function claimCreator(address token) external returns (uint256 amount) {
        amount = creatorClaimable[token][msg.sender];
        creatorClaimable[token][msg.sender] = 0;
        emit Claimed(token, msg.sender, amount, "creator");
    }

    function claimPlatform(address token, address to) external returns (uint256 amount) {
        amount = platformClaimable[token];
        platformClaimable[token] = 0;
        emit Claimed(token, to, amount, "platform");
    }

    function claimTreasury(address token) external returns (uint256 amount) {
        amount = treasuryClaimable[token];
        treasuryClaimable[token] = 0;
        emit Claimed(token, TREASURY, amount, "treasury");
    }
}
