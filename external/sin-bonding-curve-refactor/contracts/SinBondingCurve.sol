// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface IERC20Metadata {
    function decimals() external view returns (uint8);
}

/**
 * @title SinBondingCurve
 * @notice Minimal bonding curve example that handles arbitrary token decimals correctly.
 * The key principle: ALWAYS use the token's decimals() value (not hardcoded 18 or 9) when
 * converting between "whole token" units and smallest units.
 */
contract SinBondingCurve {
    IERC20Metadata public token;
    uint8 public tokenDecimals;
    uint256 public decimalsFactor;

    // Base price per whole token (in native currency smallest unit, e.g. wei)
    // e.g. 1e18 = 1 ETH per whole token
    uint256 public basePricePerWholeToken;

    constructor(address _token, uint256 _basePricePerWholeToken) {
        token = IERC20Metadata(_token);
        tokenDecimals = token.decimals();
        decimalsFactor = 10 ** uint256(tokenDecimals);
        basePricePerWholeToken = _basePricePerWholeToken;
    }

    /**
     * @notice Calculate cost (in wei) for `tokenAmount` smallest units of token
     * @param tokenAmount Amount in token smallest units (e.g., 1 for minimal unit)
     * @return price Cost in native currency smallest units (e.g., wei)
     *
     * Correct calculation: price = basePricePerWholeToken * tokenAmount / decimalsFactor
     * This guarantees correct results for tokens with 9 decimals *and* 18 decimals.
     */
    function priceForTokenAmount(uint256 tokenAmount) public view returns (uint256) {
        // Regular floor division
        return (basePricePerWholeToken * tokenAmount) / decimalsFactor;
    }

    /**
     * @notice Calculate cost (in wei) for `tokenAmount` smallest units with rounding up.
     * Useful when you want to avoid cases where tiny token amounts yield a zero price.
     */
    function priceForTokenAmountRoundedUp(uint256 tokenAmount) public view returns (uint256) {
        if (tokenAmount == 0) return 0;
        // Ceil division: (a + b - 1) / b
        uint256 numerator = (basePricePerWholeToken * tokenAmount) + (decimalsFactor - 1);
        return numerator / decimalsFactor;
    }
}
