/**
 * ship-sinc-strategy.ts
 *
 * LIVE production ship for 1inch Aqua — constant-product (XYC) SINC/WETH on Base.
 * Official @1inch SDKs only. Tokens stay in maker wallet until fill.
 *
 * Usage (LIVE):
 *   CONFIRM=LIVE MAKER_ADDRESS=0x... PRIVATE_KEY=0x... pnpm ship
 *
 * Optional dry-run:
 *   MAKER_ADDRESS=0x... pnpm ship:dry-run
 *
 * Security (post pen-test 2026-08-07):
 * - CONFIRM=LIVE required for broadcast (prevents accidental live ship)
 * - Amount / fee bounds + max caps
 * - Private key format + address match
 * - ChainId 8453 verification
 * - Registry address pinned + SDK cross-check
 * - Allowance + balance preflight before broadcast
 * - estimateGas before send
 * - No key material logged
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
  formatUnits,
  erc20Abi,
  type Hex,
  type Address as ViemAddress,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { base } from "viem/chains";

import {
  SINC,
  WETH,
  SINC_DECIMALS,
  WETH_DECIMALS,
  AQUA_REGISTRY,
  AQUA_SWAP_VM_ROUTER,
  BASE_CHAIN_ID,
  MAX_SINC_SHIP,
  MAX_WETH_SHIP,
  MIN_FEE_BPS,
  MAX_FEE_BPS,
} from "../constants";

const DRY_RUN = process.argv.includes("--dry-run");

function parsePositiveDecimal(name: string, raw: string): string {
  const t = raw.trim();
  if (!/^[0-9]+(\.[0-9]+)?$/.test(t)) {
    throw new Error(`${name} must be a non-negative decimal string, got: ${JSON.stringify(raw)}`);
  }
  if (Number(t) <= 0) {
    throw new Error(`${name} must be > 0`);
  }
  return t;
}

function parseFeeBps(raw: string): number {
  const n = Number(raw);
  if (!Number.isInteger(n) || n < MIN_FEE_BPS || n > MAX_FEE_BPS) {
    throw new Error(`FEE_BPS must be integer in [${MIN_FEE_BPS}, ${MAX_FEE_BPS}], got: ${raw}`);
  }
  return n;
}

function assertPrivateKey(pk: string): Hex {
  if (!/^0x[0-9a-fA-F]{64}$/.test(pk)) {
    throw new Error("PRIVATE_KEY must be 0x + 64 hex chars");
  }
  return pk as Hex;
}

function assertAddress(label: string, addr: string): ViemAddress {
  if (!/^0x[a-fA-F0-9]{40}$/.test(addr)) {
    throw new Error(`${label} is not a valid address: ${addr}`);
  }
  return addr as ViemAddress;
}

async function main() {
  const makerAddress = process.env.MAKER_ADDRESS;
  if (!makerAddress || !/^0x[a-fA-F0-9]{40}$/.test(makerAddress)) {
    console.error(
      "MAKER_ADDRESS required (valid 0x address).\n" +
        "Live: CONFIRM=LIVE MAKER_ADDRESS=0x... PRIVATE_KEY=0x... pnpm ship"
    );
    process.exit(1);
  }

  const SINC_AMOUNT = parsePositiveDecimal(
    "SINC_AMOUNT",
    process.env.SINC_AMOUNT || "1000"
  );
  const WETH_AMOUNT = parsePositiveDecimal(
    "WETH_AMOUNT",
    process.env.WETH_AMOUNT || "0.01"
  );
  const FEE_BPS = parseFeeBps(process.env.FEE_BPS || "30");

  if (Number(SINC_AMOUNT) > Number(MAX_SINC_SHIP)) {
    throw new Error(`SINC_AMOUNT ${SINC_AMOUNT} exceeds MAX_SINC_SHIP ${MAX_SINC_SHIP}`);
  }
  if (Number(WETH_AMOUNT) > Number(MAX_WETH_SHIP)) {
    throw new Error(`WETH_AMOUNT ${WETH_AMOUNT} exceeds MAX_WETH_SHIP ${MAX_WETH_SHIP}`);
  }

  const sincWei = parseUnits(SINC_AMOUNT, SINC_DECIMALS);
  const wethWei = parseUnits(WETH_AMOUNT, WETH_DECIMALS);

  console.log("=== 1inch Aqua SINC ship — Base ===");
  console.log(`Mode:   ${DRY_RUN ? "DRY-RUN" : "LIVE"}`);
  console.log(`Maker:  ${makerAddress}`);
  console.log(`SINC:   ${SINC_AMOUNT}`);
  console.log(`WETH:   ${WETH_AMOUNT}`);
  console.log(`Fee:    ${FEE_BPS} bps`);
  console.log("----------------------------------");

  // 1. Strategy
  const program = AquaXYCAmmStrategy.new().withFeeTokenIn(FEE_BPS).build();
  const order = Order.new({
    maker: new Address(makerAddress),
    program,
    traits: MakerTraits.default(),
  });
  const strategy = order.encode();

  if (!strategy || strategy.toString() === "0x" || strategy.toString().length < 10) {
    throw new Error("Strategy encoding empty/invalid — aborting");
  }

  // 2. Ship calldata via official SDK
  const sdkRegistry = AQUA_CONTRACT_ADDRESSES[NetworkEnum.COINBASE]?.toString?.()
    ?? String(AQUA_CONTRACT_ADDRESSES[NetworkEnum.COINBASE]);
  const sdkRouter = AQUA_SWAP_VM_CONTRACT_ADDRESSES[NetworkEnum.COINBASE]?.toString?.()
    ?? String(AQUA_SWAP_VM_CONTRACT_ADDRESSES[NetworkEnum.COINBASE]);

  // Defense in depth: SDK must match pinned constants (detect compromised/mismatched package)
  if (sdkRegistry.toLowerCase() !== AQUA_REGISTRY.toLowerCase()) {
    throw new Error(
      `SDK registry mismatch: SDK=${sdkRegistry} pinned=${AQUA_REGISTRY}`
    );
  }
  if (sdkRouter.toLowerCase() !== AQUA_SWAP_VM_ROUTER.toLowerCase()) {
    throw new Error(
      `SDK router mismatch: SDK=${sdkRouter} pinned=${AQUA_SWAP_VM_ROUTER}`
    );
  }

  const aqua = new AquaProtocolContract(AQUA_CONTRACT_ADDRESSES[NetworkEnum.COINBASE]);
  const shipTx = aqua.ship({
    app: AQUA_SWAP_VM_CONTRACT_ADDRESSES[NetworkEnum.COINBASE],
    strategy,
    amountsAndTokens: [
      { token: new Address(SINC), amount: sincWei },
      { token: new Address(WETH), amount: wethWei },
    ],
  });

  const to = shipTx.to.toString();
  const data = shipTx.data.toString() as Hex;
  const value = shipTx.value ?? 0n;

  if (to.toLowerCase() !== AQUA_REGISTRY.toLowerCase()) {
    throw new Error(`Unexpected ship target ${to}, expected ${AQUA_REGISTRY}`);
  }
  if (value !== 0n) {
    throw new Error(`Unexpected non-zero msg.value: ${value}`);
  }

  console.log("\n--- Ship tx ---");
  console.log("to:   ", to);
  console.log("data: ", data.slice(0, 10) + "… (" + data.length + " chars)");
  console.log("value:", value.toString());
  console.log("---------------\n");

  if (DRY_RUN) {
    console.log("Dry-run complete. No broadcast.");
    return;
  }

  // Live gate — prevents accidental production ships
  if (process.env.CONFIRM !== "LIVE") {
    console.error(
      "Refusing live broadcast without CONFIRM=LIVE.\n" +
        "Example: CONFIRM=LIVE MAKER_ADDRESS=0x... PRIVATE_KEY=0x... pnpm ship"
    );
    process.exit(1);
  }

  const pk = assertPrivateKey(process.env.PRIVATE_KEY || "");
  const account = privateKeyToAccount(pk);
  if (account.address.toLowerCase() !== makerAddress.toLowerCase()) {
    console.error(
      `PRIVATE_KEY address ${account.address} ≠ MAKER_ADDRESS ${makerAddress}`
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

  // ChainId hard check
  const chainId = await publicClient.getChainId();
  if (chainId !== BASE_CHAIN_ID) {
    throw new Error(`Wrong chainId ${chainId}, expected Base ${BASE_CHAIN_ID}`);
  }

  const maker = assertAddress("maker", makerAddress);
  const registry = assertAddress("registry", AQUA_REGISTRY);

  // Preflight: balances
  const [sincBal, wethBal, ethBal] = await Promise.all([
    publicClient.readContract({
      address: assertAddress("SINC", SINC),
      abi: erc20Abi,
      functionName: "balanceOf",
      args: [maker],
    }),
    publicClient.readContract({
      address: assertAddress("WETH", WETH),
      abi: erc20Abi,
      functionName: "balanceOf",
      args: [maker],
    }),
    publicClient.getBalance({ address: maker }),
  ]);

  if (sincBal < sincWei) {
    throw new Error(
      `Insufficient SINC: have ${formatUnits(sincBal, SINC_DECIMALS)}, need ${SINC_AMOUNT}`
    );
  }
  if (wethBal < wethWei) {
    throw new Error(
      `Insufficient WETH: have ${formatUnits(wethBal, WETH_DECIMALS)}, need ${WETH_AMOUNT}`
    );
  }
  if (ethBal < parseUnits("0.00005", 18)) {
    throw new Error("Insufficient ETH for gas on Base (need ~0.00005+)");
  }

  // Preflight: allowances to Aqua registry
  const [sincAllow, wethAllow] = await Promise.all([
    publicClient.readContract({
      address: assertAddress("SINC", SINC),
      abi: erc20Abi,
      functionName: "allowance",
      args: [maker, registry],
    }),
    publicClient.readContract({
      address: assertAddress("WETH", WETH),
      abi: erc20Abi,
      functionName: "allowance",
      args: [maker, registry],
    }),
  ]);

  if (sincAllow < sincWei) {
    throw new Error(
      `SINC allowance to Aqua registry too low (${formatUnits(sincAllow, SINC_DECIMALS)}). Approve first.`
    );
  }
  if (wethAllow < wethWei) {
    throw new Error(
      `WETH allowance to Aqua registry too low (${formatUnits(wethAllow, WETH_DECIMALS)}). Approve first.`
    );
  }

  // estimateGas — fail closed on bad calldata / state
  const gas = await publicClient.estimateGas({
    account: maker,
    to: registry,
    data,
    value: 0n,
  });
  console.log(`estimateGas: ${gas.toString()}`);

  console.log(`Broadcasting LIVE from ${account.address} ...`);
  const hash = await walletClient.sendTransaction({
    to: registry,
    data,
    value: 0n,
    gas: (gas * 120n) / 100n, // 20% headroom
  });
  console.log("Tx hash:", hash);

  const receipt = await publicClient.waitForTransactionReceipt({ hash });
  console.log("Status:", receipt.status);
  if (receipt.status !== "success") {
    process.exit(1);
  }
  console.log("LIVE ship complete. Strategy on-chain and available for fills.");
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
