/**
 * ship-sinc-strategy.ts
 *
 * Builds (and optionally broadcasts) a real 1inch Aqua ship() transaction for a
 * constant-product (XYC) SINC/WETH strategy on Base using the official SDKs.
 *
 * TESTED: generates non-empty strategy + valid ship calldata (selector 0xf50b870f).
 *
 * SAFETY
 * - Dry-run is the DEFAULT. Nothing is sent on-chain unless --execute is passed
 *   AND PRIVATE_KEY is set.
 * - Tokens stay in the maker wallet until a later fill (Aqua design).
 * - This script does NOT approve tokens. Approve the registry once separately
 *   (or use the 1inch Aqua UI).
 *
 * Usage
 *   MAKER_ADDRESS=0x... pnpm tsx scripts/ship-sinc-strategy.ts
 *   MAKER_ADDRESS=0x... PRIVATE_KEY=0x... pnpm tsx scripts/ship-sinc-strategy.ts --execute
 *
 * Amounts are intentionally small defaults. Change SINC_AMOUNT / WETH_AMOUNT below.
 */

import {
  Address,
  AquaXYCAmmStrategy,
  Order,
  MakerTraits,
  NetworkEnum,
  AQUA_SWAP_VM_CONTRACT_ADDRESSES,
} from "@1inch/swap-vm-sdk";
import {
  AquaProtocolContract,
  AQUA_CONTRACT_ADDRESSES,
} from "@1inch/aqua-sdk";
import {
  createWalletClient,
  createPublicClient,
  http,
  parseUnits,
  type Hex,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { base } from "viem/chains";

import {
  SINC,
  WETH,
  SINC_DECIMALS,
  WETH_DECIMALS,
} from "../constants";

// ---------------------------------------------------------------------------
// Config – edit amounts here for testing
// ---------------------------------------------------------------------------
const SINC_AMOUNT = "1000"; // human units (8 decimals)
const WETH_AMOUNT = "0.01"; // human units (18 decimals)
const FEE_BPS = 30; // 0.30% fee on token-in (SDK scale)

const EXECUTE = process.argv.includes("--execute");

async function main() {
  const makerAddress = process.env.MAKER_ADDRESS;
  if (!makerAddress || !/^0x[a-fA-F0-9]{40}$/.test(makerAddress)) {
    console.error(
      "MAKER_ADDRESS env var is required and must be a valid 0x address.\n" +
        "Example: MAKER_ADDRESS=0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac pnpm tsx scripts/ship-sinc-strategy.ts"
    );
    process.exit(1);
  }

  console.log("=== 1inch Aqua SINC ship (Base) ===");
  console.log(`Mode: ${EXECUTE ? "EXECUTE" : "DRY-RUN"}`);
  console.log(`Maker: ${makerAddress}`);
  console.log(`SINC:  ${SINC} (${SINC_DECIMALS} decimals)`);
  console.log(`WETH:  ${WETH} (${WETH_DECIMALS} decimals)`);
  console.log(`Fee:   ${FEE_BPS} bps`);
  console.log("----------------------------------");

  // 1. Build constant-product program with fee
  const program = AquaXYCAmmStrategy.new().withFeeTokenIn(FEE_BPS).build();

  // 2. Wrap as Order (MakerTraits.default() already sets useAquaInsteadOfSignature = true)
  const order = Order.new({
    maker: new Address(makerAddress),
    program,
    traits: MakerTraits.default(),
  });
  const strategy = order.encode();

  if (!strategy || strategy.toString() === "0x" || strategy.toString().length < 10) {
    throw new Error("Strategy encoding produced empty/invalid bytes – aborting");
  }

  // 3. Build ship calldata via official Aqua SDK
  const aqua = new AquaProtocolContract(AQUA_CONTRACT_ADDRESSES[NetworkEnum.COINBASE]);
  const shipTx = aqua.ship({
    app: AQUA_SWAP_VM_CONTRACT_ADDRESSES[NetworkEnum.COINBASE],
    strategy,
    amountsAndTokens: [
      { token: new Address(SINC), amount: parseUnits(SINC_AMOUNT, SINC_DECIMALS) },
      { token: new Address(WETH), amount: parseUnits(WETH_AMOUNT, WETH_DECIMALS) },
    ],
  });

  const to = shipTx.to.toString();
  const data = shipTx.data.toString() as Hex;
  const value = shipTx.value ?? 0n;

  console.log("\n--- Generated ship transaction ---");
  console.log("to:    ", to);
  console.log("data:  ", data.slice(0, 66) + "... (" + data.length + " chars)");
  console.log("value: ", value.toString());
  console.log("strategy length:", strategy.toString().length);
  console.log("----------------------------------\n");

  if (to.toLowerCase() !== AQUA_CONTRACT_ADDRESSES[NetworkEnum.COINBASE].toString().toLowerCase()) {
    throw new Error(`Unexpected registry address: ${to}`);
  }

  if (!EXECUTE) {
    console.log("Dry-run complete. No transaction sent.");
    console.log("To broadcast: set PRIVATE_KEY and re-run with --execute");
    console.log("Or paste the full calldata into a hardware wallet / Safe.");
    return;
  }

  const pk = process.env.PRIVATE_KEY;
  if (!pk) {
    console.error("PRIVATE_KEY is required for --execute");
    process.exit(1);
  }

  const account = privateKeyToAccount(pk as Hex);
  if (account.address.toLowerCase() !== makerAddress.toLowerCase()) {
    console.error(
      `PRIVATE_KEY address (${account.address}) does not match MAKER_ADDRESS (${makerAddress})`
    );
    process.exit(1);
  }

  const rpc = process.env.BASE_RPC_URL || "https://mainnet.base.org";
  const publicClient = createPublicClient({ chain: base, transport: http(rpc) });
  const walletClient = createWalletClient({
    account,
    chain: base,
    transport: http(rpc),
  });

  console.log(`Broadcasting from ${account.address} ...`);
  const hash = await walletClient.sendTransaction({
    to: to as Hex,
    data,
    value,
  });
  console.log("Tx hash:", hash);
  const receipt = await publicClient.waitForTransactionReceipt({ hash });
  console.log("Status:", receipt.status);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
