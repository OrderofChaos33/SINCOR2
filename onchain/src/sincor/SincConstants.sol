// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

library SincConstants {
    uint256 internal constant BASE_CHAIN_ID = 8453;
    address internal constant TREASURY_SEED_ADDRESS = 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac;
    uint16 internal constant MAX_SLIPPAGE_BPS = 50;
    uint16 internal constant FEE_SPLIT_BUYBACK_BPS = 7000;
    uint16 internal constant FEE_SPLIT_TREASURY_BPS = 3000;
    uint16 internal constant BPS_DENOM = 10_000;
}
