import {
  createWalletClient,
  createPublicClient,
  http,
  parseEther,
  parseAbi,
  Address,
  encodeDeployData,
  getContractAddress,
  keccak256,
  toHex,
} from 'viem';
import { privateKeyToAccount } from 'viem/accounts';
import { base } from 'viem/chains';

// --- CONFIGURATION ---
// Canonical Base addresses from CANONICAL_ADDRESSES.md. Env overrides always win.
const RPC_URL = process.env.BASE_RPC_URL || 'https://mainnet.base.org';
const WETH_ADDRESS: Address = '0x4200000000000000000000000000000000000006';

const SINC_ADDRESS = (process.env.SINC_TOKEN_ADDRESS ||
  '0xe1D836087F6573b665d25CE088793E916D7892f8') as Address;
const AXM_ADDRESS = (process.env.AXM_TOKEN_ADDRESS ||
  '0x4c3fb66f14fbaa2088c9ae91017ba770da53715a') as Address;
const TASK_ESCROW_ADDRESS = (process.env.SINCOR_TASK_ESCROW ||
  '0x0000000000000000000000000000000000000000') as Address;

const POOL_MANAGER_ADDRESS = (process.env.UNISWAP_V4_POOL_MANAGER ||
  '0x498581fF718922c3f8e6A244956aF099B2652b2b') as Address;
const POSITION_MANAGER_ADDRESS = (process.env.UNISWAP_V4_POSITION_MANAGER ||
  '0x7C5f5A4bBd8fD63184577525326123B519429bDc') as Address;
const CREATE2_DEPLOYER: Address = '0x4e59b44847b379578588920cA78FbF26c0B4956C';

// Uniswap v4 Hook Bit Flags
const AFTER_SWAP_FLAG = 1n << 6n; // 0x40 - bit 6 enables afterSwap callback
const ALL_HOOK_FLAGS_MASK = (1n << 14n) - 1n; // Lower 14 bits mask

const ZERO_PK = '0x0000000000000000000000000000000000000000000000000000000000000001';
const account = privateKeyToAccount((process.env.ROOT_AGENT_PRIVATE_KEY || ZERO_PK) as `0x${string}`);
const publicClient = createPublicClient({ chain: base, transport: http(RPC_URL) });
const walletClient = createWalletClient({ account, chain: base, transport: http(RPC_URL) });

// --- ABIS ---
const poolManagerAbi = parseAbi([
  'struct PoolKey { address currency0; address currency1; uint24 fee; int24 tickSpacing; address hooks; }',
  'function initialize(PoolKey memory key, uint160 sqrtPriceX96, bytes calldata hookData) external returns (int24 tick)',
]);

const create2DeployerAbi = parseAbi([
  'function deploy(uint256 value, bytes32 salt, bytes calldata code) external returns (address)',
]);

const erc20Abi = parseAbi(['function approve(address spender, uint256 amount) external returns (bool)']);

// Replace bytecode with `forge inspect BribeLoopHook bytecode` before a live deploy.
const bribeLoopHookArtifact = {
  abi: parseAbi([
    'constructor(address _poolManager, address _taskEscrow)',
    'function setBribeBps(uint256 _bribeBps) external',
    'function setTaskEscrow(address _taskEscrow) external',
    'function bribeBps() external view returns (uint256)',
  ]),
  bytecode: (process.env.BRIBE_LOOP_HOOK_BYTECODE || '0x') as `0x${string}`,
};

// --- HOOK SALT MINER ---
function mineHookSalt(
  deployer: Address,
  initCode: `0x${string}`,
  requiredFlags: bigint
): { salt: `0x${string}`; predictedAddress: Address } {
  console.log(`[Hook Miner] Mining CREATE2 salt for AFTER_SWAP flag (0x40)...`);
  let nonce = 0n;
  const initCodeHash = keccak256(initCode);

  while (true) {
    const salt = toHex(nonce, { size: 32 });
    const predictedAddress = getContractAddress({
      from: deployer,
      salt,
      bytecodeHash: initCodeHash,
      opcode: 'CREATE2',
    });

    const addrBigInt = BigInt(predictedAddress);
    if ((addrBigInt & ALL_HOOK_FLAGS_MASK) === requiredFlags) {
      console.log(`[+] Salt found after ${nonce} iterations!`);
      console.log(`    Salt: ${salt}`);
      console.log(`    Hook Address: ${predictedAddress}`);
      return { salt, predictedAddress };
    }
    nonce++;
    if (nonce % 50000n === 0n) {
      console.log(`    ... still mining (${nonce} salts tried)`);
    }
  }
}

// --- DEPLOY HOOK ---
async function deployBribeLoopHook(): Promise<Address> {
  console.log(`\n==================================================`);
  console.log(`[Uniswap v4] Mining & Deploying BribeLoopHook`);
  console.log(`==================================================`);

  if (!bribeLoopHookArtifact.bytecode || bribeLoopHookArtifact.bytecode === '0x' || bribeLoopHookArtifact.bytecode.length < 10) {
    throw new Error(
      'BRIBE_LOOP_HOOK_BYTECODE is missing. Compile with `forge inspect BribeLoopHook bytecode` and set the env var.'
    );
  }
  if (TASK_ESCROW_ADDRESS === '0x0000000000000000000000000000000000000000') {
    throw new Error('Set SINCOR_TASK_ESCROW to the deployed SincorTaskEscrow address.');
  }

  const initCode = encodeDeployData({
    abi: bribeLoopHookArtifact.abi,
    bytecode: bribeLoopHookArtifact.bytecode,
    args: [POOL_MANAGER_ADDRESS, TASK_ESCROW_ADDRESS],
  });

  const { salt, predictedAddress } = mineHookSalt(CREATE2_DEPLOYER, initCode, AFTER_SWAP_FLAG);

  const codeAtAddr = await publicClient.getBytecode({ address: predictedAddress });
  if (codeAtAddr && codeAtAddr !== '0x') {
    console.log(`[+] BribeLoopHook already deployed at ${predictedAddress}`);
    return predictedAddress;
  }

  console.log(`[+] Executing CREATE2 deployment via ${CREATE2_DEPLOYER}...`);
  const txHash = await walletClient.writeContract({
    address: CREATE2_DEPLOYER,
    abi: create2DeployerAbi,
    functionName: 'deploy',
    args: [0n, salt, initCode],
  });

  await publicClient.waitForTransactionReceipt({ hash: txHash });
  console.log(`[+] BribeLoopHook deployed to: ${predictedAddress}`);
  return predictedAddress;
}

// --- POOL INITIALIZATION ---
function sortCurrencies(currencyA: Address, currencyB: Address): { currency0: Address; currency1: Address } {
  return currencyA.toLowerCase() < currencyB.toLowerCase()
    ? { currency0: currencyA, currency1: currencyB }
    : { currency0: currencyB, currency1: currencyA };
}

const SQRT_PRICE_1_TO_1 = 79228162514264337593543950336n;

interface PoolConfig {
  name: string;
  tokenAddress: Address;
  fee: number;
  tickSpacing: number;
  hookAddress: Address;
}

async function initializeAndSeedPool(config: PoolConfig) {
  console.log(`\n==================================================`);
  console.log(`[Uniswap v4] Initializing Pool: ${config.name}`);
  console.log(`==================================================`);

  const { currency0, currency1 } = sortCurrencies(config.tokenAddress, WETH_ADDRESS);
  const poolKey = {
    currency0,
    currency1,
    fee: config.fee,
    tickSpacing: config.tickSpacing,
    hooks: config.hookAddress,
  };

  console.log(`[+] Currency0: ${currency0}`);
  console.log(`[+] Currency1: ${currency1}`);
  console.log(`[+] Hook:      ${config.hookAddress}`);

  console.log(`[+] Approving tokens...`);
  const approveTx1 = await walletClient.writeContract({
    address: config.tokenAddress,
    abi: erc20Abi,
    functionName: 'approve',
    args: [POSITION_MANAGER_ADDRESS, parseEther('1000000')],
  });
  await publicClient.waitForTransactionReceipt({ hash: approveTx1 });

  console.log(`[+] Initializing pool on PoolManager...`);
  try {
    const initTx = await walletClient.writeContract({
      address: POOL_MANAGER_ADDRESS,
      abi: poolManagerAbi,
      functionName: 'initialize',
      args: [poolKey, SQRT_PRICE_1_TO_1, '0x'],
    });
    const receipt = await publicClient.waitForTransactionReceipt({ hash: initTx });
    console.log(`[+] Pool Initialized! Transaction: ${receipt.transactionHash}`);
  } catch (err: any) {
    console.warn(`[!] Initialization skipped or pool active: ${err.message}`);
  }
}

async function main() {
  if (!process.env.ROOT_AGENT_PRIVATE_KEY) {
    console.error(`[!] Error: Set ROOT_AGENT_PRIVATE_KEY in the environment.`);
    process.exit(1);
  }

  const hookAddress = await deployBribeLoopHook();

  await initializeAndSeedPool({
    name: 'SINC / WETH (BribeLoop)',
    tokenAddress: SINC_ADDRESS,
    fee: 3000,
    tickSpacing: 60,
    hookAddress,
  });

  await initializeAndSeedPool({
    name: 'AXM / WETH (BribeLoop)',
    tokenAddress: AXM_ADDRESS,
    fee: 3000,
    tickSpacing: 60,
    hookAddress,
  });

  console.log(`\n[+] Uniswap v4 Hook & Pool Deployment Pipeline Finished.`);
}

main().catch((err) => {
  console.error('[!] Script execution failed:', err);
  process.exit(1);
});
