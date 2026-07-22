// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @notice ERC-4626-lite mock of Fluid fToken. 1:1 share accounting.
contract MockFToken {
    IERC20 public immutable asset;
    mapping(address => uint256) public balanceOf;
    uint256 public totalShares;

    constructor(IERC20 _asset) {
        asset = _asset;
    }

    function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
        shares = assets;
        asset.transferFrom(msg.sender, address(this), assets);
        balanceOf[receiver] += shares;
        totalShares += shares;
    }

    function redeem(uint256 shares, address receiver, address owner) external returns (uint256 assets) {
        require(balanceOf[owner] >= shares, "insufficient shares");
        balanceOf[owner] -= shares;
        totalShares -= shares;
        assets = shares;
        asset.transfer(receiver, assets);
    }

    function convertToAssets(uint256 shares) external pure returns (uint256) {
        return shares;
    }

    function convertToShares(uint256 assets) external pure returns (uint256) {
        return assets;
    }
}

/// @notice Behavior mock of Fluid DexT1 (col + debt ops), pool order token0/token1.
/// @dev    Tests etch this at the adapter's hardcoded LIQUIDITY address so the
///         mock's transferFrom(msg.sender) spends the adapter's LIQUIDITY allowance —
///         exactly how the real DEX settles through the Liquidity layer.
contract MockDexT1 {
    IERC20 public immutable token0;
    IERC20 public immutable token1;
    mapping(address => uint256) public colShares;
    mapping(address => uint256) public debtShares;

    constructor(IERC20 _t0, IERC20 _t1) {
        token0 = _t0;
        token1 = _t1;
    }

    function deposit(uint256 t0Amt, uint256 t1Amt, uint256 minShares, bool) external payable returns (uint256 shares) {
        shares = t0Amt + t1Amt;
        require(shares >= minShares, "minShares");
        if (t0Amt > 0) token0.transferFrom(msg.sender, address(this), t0Amt);
        if (t1Amt > 0) token1.transferFrom(msg.sender, address(this), t1Amt);
        colShares[msg.sender] += shares;
    }

    function withdraw(uint256 t0Amt, uint256 t1Amt, uint256 maxShares, address to) external returns (uint256 shares) {
        shares = t0Amt + t1Amt;
        require(shares <= maxShares, "maxShares");
        require(colShares[msg.sender] >= shares, "col");
        colShares[msg.sender] -= shares;
        if (t0Amt > 0) token0.transfer(to, t0Amt);
        if (t1Amt > 0) token1.transfer(to, t1Amt);
    }

    function borrowPerfect(uint256 shares, uint256 min0, uint256 min1, address to) external returns (uint256, uint256) {
        debtShares[msg.sender] += shares;
        if (min0 > 0) token0.transfer(to, min0);
        if (min1 > 0) token1.transfer(to, min1);
        return (min0, min1);
    }

    function payback(uint256 t0Amt, uint256 t1Amt, uint256 minShares, bool) external payable returns (uint256 shares) {
        shares = t0Amt + t1Amt;
        require(shares >= minShares, "minShares");
        if (t0Amt > 0) token0.transferFrom(msg.sender, address(this), t0Amt);
        if (t1Amt > 0) token1.transferFrom(msg.sender, address(this), t1Amt);
        uint256 d = debtShares[msg.sender];
        debtShares[msg.sender] = shares > d ? 0 : d - shares;
    }
}
