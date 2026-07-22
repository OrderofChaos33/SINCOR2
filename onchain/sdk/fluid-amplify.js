/* fluid-amplify.js — getsincor.com dashboard drop-in
 * "Amplify SINC Liquidity" button: connect → approve SINC/USDC → deposit to SincFluidAdapter.
 * Live position value + multiplier read from the adapter + Fluid fUSDC (chain 8453).
 * Deps: ethers v6 (CDN: https://cdn.jsdelivr.net/npm/ethers@6.13.4/dist/ethers.umd.min.js)
 */

const CHAIN_ID = 8453;
const SINC   = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"; // 8 decimals
const USDC   = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"; // 6 decimals
const ADAPTER = "0x0000000000000000000000000000000000000000"; // TODO: set post-deploy
const F_USDC = "0xf42f5795D9ac7e9D757dB633D693cD548Cfd9169";
const DEX_RESOLVER = "0x11D80CfF056Cef4F9E6d23da8672fE9873e5cC07";

const ERC20_ABI = [
  "function approve(address spender, uint256 amount) returns (bool)",
  "function allowance(address owner, address spender) view returns (uint256)",
];
const ADAPTER_ABI = [
  "function depositUSDC(uint256 assets) returns (uint256)",
  "function withdrawUSDC(uint256 shares) returns (uint256)",
  "function supplyToDex(uint256 sincAmt, uint256 usdcAmt, uint256 minShares) returns (uint256)",
  "function fUsdcShares(address user) view returns (uint256)",
  "function dexColShares(address user) view returns (uint256)",
  "function userValueUSDC(address user) view returns (uint256)",
  "function fluidDex() view returns (address)",
];
const FTOKEN_ABI = [
  "function convertToAssets(uint256 shares) view returns (uint256)",
  "function totalSupply() view returns (uint256)",
];
// DexResolver.getDexEntireData(dexAddr) returns the full pool struct (state, fee,
// reserves, col/debt totals) — decode with the ABI from Basescan of DEX_RESOLVER.
const DEX_RESOLVER_ABI = ["function getDexEntireData(address dex_) view returns (tuple())"]; // placeholder: replace with full struct ABI

let provider, signer, account;

export async function connect() {
  if (!window.ethereum) throw new Error("No wallet");
  provider = new ethers.BrowserProvider(window.ethereum);
  const net = await provider.getNetwork();
  if (Number(net.chainId) !== CHAIN_ID) {
    await window.ethereum.request({ method: "wallet_switchEthereumChain", params: [{ chainId: "0x2105" }] });
  }
  signer = await provider.getSigner();
  account = await signer.getAddress();
  return account;
}

async function ensureAllowance(token, amount) {
  const c = new ethers.Contract(token, ERC20_ABI, signer);
  const cur = await c.allowance(account, ADAPTER);
  if (cur < amount) {
    const tx = await c.approve(ADAPTER, amount);
    await tx.wait();
  }
}

// ---- Button flow: Amplify ----
// usdcAmount: human units (e.g. 100 = 100 USDC). Deposits the USDC leg into Fluid
// fUSDC now; once fluidDex() != 0x0 the button also offers the SINC+USDC pair leg.
export async function amplify(usdcAmount) {
  const assets = ethers.parseUnits(String(usdcAmount), 6);
  await ensureAllowance(USDC, assets);
  const adapter = new ethers.Contract(ADAPTER, ADAPTER_ABI, signer);
  const tx = await adapter.depositUSDC(assets);
  await tx.wait();
  return refreshPanel();
}

// Pair leg (post-Fluid-governance): supplies SINC+USDC as smart collateral.
export async function amplifyPair(sincAmount, usdcAmount, slippageBps = 50) {
  const sincAmt = ethers.parseUnits(String(sincAmount), 8);
  const usdcAmt = ethers.parseUnits(String(usdcAmount), 6);
  await ensureAllowance(SINC, sincAmt);
  await ensureAllowance(USDC, usdcAmt);
  const adapter = new ethers.Contract(ADAPTER, ADAPTER_ABI, signer);
  if ((await adapter.fluidDex()) === ethers.ZeroAddress) throw new Error("SINC-USDC Fluid DEX not listed yet");
  // minShares=0 acceptable at bootstrap (treasury seeds first); tighten post-seed
  const tx = await adapter.supplyToDex(sincAmt, usdcAmt, 0);
  await tx.wait();
  return refreshPanel();
}

export async function unamplify(shares) {
  const adapter = new ethers.Contract(ADAPTER, ADAPTER_ABI, signer);
  const tx = await adapter.withdrawUSDC(shares);
  await tx.wait();
  return refreshPanel();
}

// ---- Live reads for the dashboard ----
export async function refreshPanel() {
  const adapter = new ethers.Contract(ADAPTER, ADAPTER_ABI, provider);
  const fUsdc = new ethers.Contract(F_USDC, FTOKEN_ABI, provider);
  const shares = await adapter.fUsdcShares(account);
  const valueUsdc = await fUsdc.convertToAssets(shares);
  const colShares = await adapter.dexColShares(account);
  const dex = await adapter.fluidDex();
  return {
    lendingShares: shares.toString(),
    lendingValueUSDC: ethers.formatUnits(valueUsdc, 6),
    smartColShares: colShares.toString(),
    pairLive: dex !== ethers.ZeroAddress,
    // multiplier: user's effective leverage = (col value + borrowed redeployed) / own capital.
    // Per-user borrowing is treasury-gated by design; show target 3-5x as config, not live fact.
    targetMultiplier: "3-5x (treasury smart-debt stage)",
  };
}

/* HTML:
 * <button id="amplifyBtn">Amplify SINC Liquidity</button>
 * <script type="module">
 *   import { connect, amplify, refreshPanel } from "./fluid-amplify.js";
 *   document.getElementById("amplifyBtn").onclick = async () => {
 *     await connect();
 *     const r = await amplify(100); // 100 USDC
 *     console.log(r);
 *   };
 * </script>
 */
