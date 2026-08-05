/**
 * Canonical addresses for 1inch Aqua + SINC on Base (chainId 8453).
 * All values are public, verified, and intended for Gemini / external audit.
 * Do not change these without cross-checking 1inch docs + Basescan.
 */

export const BASE_CHAIN_ID = 8453;

export const AQUA_REGISTRY = "0x1111113ccf1426a8e30e2bff5e005d929bf6a90a" as const;
export const AQUA_SWAP_VM_ROUTER = "0x111111338c5091e8440b67b168bae16a668ac0de" as const;
export const SWAP_VM = "0x8fdd04dbf6111437b44bbca99c28882434e0958f" as const;

export const SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7" as const;
export const WETH = "0x4200000000000000000000000000000000000006" as const;
export const USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913" as const;

export const SINC_DECIMALS = 8;
export const WETH_DECIMALS = 18;
export const USDC_DECIMALS = 6;

/** Official 1inch NetworkEnum value for Base (Coinbase Base). */
export const NETWORK_ENUM_BASE = 8453; // matches @1inch/aqua-sdk NetworkEnum when present
