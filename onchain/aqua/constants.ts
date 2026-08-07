/**
 * Canonical addresses for 1inch Aqua + SINC on Base (chainId 8453).
 * All values public. Cross-check 1inch docs + Basescan before changing.
 *
 * NOTE: Do NOT use superseded registry/router addresses from older 1inch docs.
 * Canonical vanity pair (2026-07-19 / 2026-07-26):
 *   Registry  0x1111113ccf1426a8e30e2bff5e005d929bf6a90a
 *   SwapVM    0x111111338c5091e8440b67b168bae16a668ac0de
 */

export const BASE_CHAIN_ID = 8453;

/** Aqua registry (AquaRouter) — same address on all 13 Aqua chains */
export const AQUA_REGISTRY =
  "0x1111113ccf1426a8e30e2bff5e005d929bf6a90a" as const;

/** AquaSwapVMRouter v1.0.2 */
export const AQUA_SWAP_VM_ROUTER =
  "0x111111338c5091e8440b67b168bae16a668ac0de" as const;

export const SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7" as const;
export const WETH = "0x4200000000000000000000000000000000000006" as const;
export const USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913" as const;

export const SINC_DECIMALS = 8;
export const WETH_DECIMALS = 18;
export const USDC_DECIMALS = 6;

/** Hard caps for a single ship (override via env only after conscious decision) */
export const MAX_SINC_SHIP = process.env.MAX_SINC_SHIP || "100000"; // 100k SINC
export const MAX_WETH_SHIP = process.env.MAX_WETH_SHIP || "5"; // 5 WETH

export const MIN_FEE_BPS = 1;
export const MAX_FEE_BPS = 1000; // 10%

/** Official 1inch NetworkEnum-compatible Base id */
export const NETWORK_ENUM_BASE = 8453;
