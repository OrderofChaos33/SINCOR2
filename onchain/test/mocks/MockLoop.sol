// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ISincPriceOracle, ISincSwapRouter} from "../../src/interfaces/ISincLoop.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {FullMath} from "@uniswap/v4-core/src/libraries/FullMath.sol";

contract MockOracle is ISincPriceOracle {
    uint256 public price; // USDC 6dp per whole SINC

    constructor(uint256 initialPrice) {
        price = initialPrice;
    }

    function setPrice(uint256 p) external {
        price = p;
    }

    function sincPriceUSDC() external view returns (uint256) {
        return price;
    }
}

/// @dev Fixed-price router with configurable fee; must be funded with both tokens.
///      Decimal-aware: normalizes SINC amounts by the token's actual decimals.
contract MockSwapRouter is ISincSwapRouter {
    using SafeERC20 for IERC20;

    IERC20 public immutable SINC;
    IERC20 public immutable USDC;
    ISincPriceOracle public oracle;
    uint256 public immutable sincUnit;
    uint256 public feeBps = 30; // 0.30%

    constructor(IERC20 _sinc, IERC20 _usdc, ISincPriceOracle _oracle) {
        SINC = _sinc;
        USDC = _usdc;
        oracle = _oracle;
        uint8 d;
        try IERC20Metadata(address(_sinc)).decimals() returns (uint8 dec_) { d = dec_; } catch { d = 18; }
        sincUnit = 10 ** d;
    }

    function setFeeBps(uint256 f) external {
        feeBps = f;
    }

    function swapUSDCForSINC(uint256 usdcIn) external returns (uint256 sincOut) {
        USDC.safeTransferFrom(msg.sender, address(this), usdcIn);
        uint256 net = usdcIn - FullMath.mulDiv(usdcIn, feeBps, 10_000);
        sincOut = FullMath.mulDiv(net, sincUnit, oracle.sincPriceUSDC());
        SINC.safeTransfer(msg.sender, sincOut);
    }

    function swapSINCForUSDC(uint256 sincIn) external returns (uint256 usdcOut) {
        SINC.safeTransferFrom(msg.sender, address(this), sincIn);
        uint256 gross = FullMath.mulDiv(sincIn, oracle.sincPriceUSDC(), sincUnit);
        usdcOut = gross - FullMath.mulDiv(gross, feeBps, 10_000);
        USDC.safeTransfer(msg.sender, usdcOut);
    }
}
