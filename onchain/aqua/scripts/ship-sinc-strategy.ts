/**
 * ship-sinc-strategy.ts
 *
 * Builds (and optionally sends) a 1inch Aqua `ship()` transaction for a simple
 * constant-product (XYC) SINC/WETH strategy on Base.
 *
 * SAFETY:
 * - Dry-run is the DEFAULT. No transaction is sent unless --execute is passed
 *   AND PRIVATE_KEY is set in the environment.
 * - Tokens never leave the maker wallet until a later fill occurs.
 * - This script does not approve tokens; do that separately (or via 1inch UI).
 *
 * Usage:
 *   pnpm tsx scripts/ship-sinc-strategy.ts              # dry-run
 *   pnpm tsx scripts/ship-sinc-strategy.ts --execute    # real tx (requires key)
 *
 * For production concentrated ranges around the $1.50 floor, prefer the
 * official 1inch Aqua UI or full SwapVM program builders. This script is the
 * minimal, auditable starting point.
 */

import { createWalletClient, createPublicClient, http, parseUnits, type Hex } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { base } from "viem/chains";
import {
  AQUA_REGISTRY,
  AQUA_SWAP_VM_ROUTER,
  SINC,
  WETH,
  SINC_DECIMALS,
  WETH_DECIMALS,
} from "../constants";

// ---------------------------------------------------------------------------
// Configuration – change amounts here for testing
// ---------------------------------------------------------------------------
const SINC_AMOUNT = "1000";   // human-readable; 1000 SINC (8 decimals)
const WETH_AMOUNT = "0.01";   // human-readable; start tiny

const EXECUTE = process.argv.includes("--execute");

// ---------------------------------------------------------------------------
// Minimal ABI for Aqua.ship (exact signature from official docs)
// ship(address app, bytes strategy, address[] tokens, uint256[] amounts)
// ---------------------------------------------------------------------------
const AQUA_ABI = [
  {
    name: "ship",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "app", type: "address" },
      { name: "strategy", type: "bytes" },
      { name: "tokens", type: "address[]" },
      { name: "amounts", type: "uint256[]" },
    ],
    outputs: [{ name: "strategyHash", type: "bytes32" }],
  },
] as const;

async function main() {
  console.log("=== 1inch Aqua SINC ship script (Base) ===");
  console.log(`Mode: ${EXECUTE ? "EXECUTE (real tx)" : "DRY-RUN (calldata only)"}`);
  console.log(`Registry: ${AQUA_REGISTRY}`);
  console.log(`Router (app): ${AQUA_SWAP_VM_ROUTER}`);
  console.log(`SINC: ${SINC} (${SINC_DECIMALS} decimals)`);
  console.log(`WETH: ${WETH} (${WETH_DECIMALS} decimals)`);
  console.log("------------------------------------------");

  // For a real strategy you would build a full SwapVM Order / Program here
  // using @1inch/swap-vm-sdk (AquaXYCAmmStrategy or concentrated builders).
  // This placeholder is intentionally simple and clearly marked so auditors
  // can see that strategy encoding is the only non-trivial part.
  //
  // In production: replace `strategyBytes` with the output of the official
  // SDK Order.encode() after setting maker, fee, salt, etc.
  const strategyBytes = "0x" as Hex; // PLACEHOLDER – replace with real encoded strategy

  if (strategyBytes === "0x") {
    console.warn(
      "\n[WARNING] strategyBytes is still the empty placeholder.\n" +
        "This script will not produce a valid ship until you supply a real\n" +
        "SwapVM-encoded strategy (use @1inch/swap-vm-sdk or the 1inch Aqua UI).\n" +
        "The calldata structure and addresses are correct; only the strategy payload is missing.\n"
    );
  }

  const tokens = [SINC, WETH] as const;
  const amounts = [
    parseUnits(SINC_AMOUNT, SINC_DECIMALS),
    parseUnits(WETH_AMOUNT, WETH_DECIMALS),
  ];

  console.log(`Amounts: ${SINC_AMOUNT} SINC + ${WETH_AMOUNT} WETH`);

  // Build the call using viem for transparency
  const { encodeFunctionData } = await import("viem");
  const data = encodeFunctionData({
    abi: AQUA_ABI,
    functionName: "ship",
    args: [AQUA_SWAP_VM_ROUTER, strategyBytes, [...tokens], amounts],
  });

  console.log("\n--- Transaction data (review carefully) ---");
  console.log("to:", AQUA_REGISTRY);
  console.log("data:", data);
  console.log("value: 0");
  console.log("------------------------------------------\n");

  if (!EXECUTE) {
    console.log("Dry-run complete. No transaction sent.");
    console.log("To execute: set PRIVATE_KEY and re-run with --execute");
    console.log("Or paste the calldata into a hardware wallet / Safe.");
    return;
  }

  const pk = process.env.PRIVATE_KEY;
  if (!pk) {
    console.error("PRIVATE_KEY env var is required for --execute");
    process.exit(1);
  }

  const account = privateKeyToAccount(pk as Hex);
  const publicClient = createPublicClient({
    chain: base,
    transport: http(process.env.BASE_RPC_URL || "https://mainnet.base.org"),
  });
  const walletClient = createWalletClient({
    account,
    chain: base,
    transport: http(process.env.BASE_RPC_URL || "https://mainnet.base.org"),
  });

  console.log(`Sending from: ${account.address}`);
  const hash = await walletClient.sendTransaction({
    to: AQUA_REGISTRY,
    data,
    value: 0n,
  });
  console.log("Tx hash:", hash);
  const receipt = await publicClient.waitForTransactionReceipt({ hash });
  console.log("Status:", receipt.status);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
