#!/usr/bin/env bash
# =============================================================================
# Base Sepolia deploy (testnet loops): SharedLiquidityHook + LiquidityAmplifierHook
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

# Base Sepolia defaults (override in onchain/.env for your test deployment)
export BASE_SEPOLIA_RPC="${BASE_SEPOLIA_RPC_URL:-https://sepolia.base.org}"
export SINC_TOKEN="${SINC_TOKEN:-0x0000000000000000000000000000000000000000}"
export USDC_TOKEN="${USDC_TOKEN:-0x0000000000000000000000000000000000000000}"
export POOL_MANAGER="${POOL_MANAGER:-0x498581fF718922c3f8e6A244956aF099B2652b2b}"
export TREASURY="${TREASURY:-0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac}"
export DEPLOY_VAULT="${DEPLOY_VAULT:-1}"
export DEPLOY_HOOK=0
export DEPLOY_LOOP_INFRA=0
export DEPLOY_LENDING=0

if [[ -z "${PRIVATE_KEY:-}" && -f .env ]]; then
  set -a; source .env; set +a
fi
[[ -n "${PRIVATE_KEY:-}" ]] || { echo "ERROR: PRIVATE_KEY not set in env/onchain/.env"; exit 1; }
[[ "$SINC_TOKEN" != "0x0000000000000000000000000000000000000000" ]] || {
  echo "ERROR: set SINC_TOKEN for Base Sepolia";
  exit 1;
}
[[ "$USDC_TOKEN" != "0x0000000000000000000000000000000000000000" ]] || {
  echo "ERROR: set USDC_TOKEN for Base Sepolia";
  exit 1;
}

echo "RPC: $BASE_SEPOLIA_RPC"
echo "Deployer: $(cast wallet address "$PRIVATE_KEY")"

# 1) Deploy vault only (hook deployment handled separately via CREATE2 scripts below)
forge script script/Deploy.s.sol --broadcast --rpc-url "$BASE_SEPOLIA_RPC" -vvv

# 2) CREATE2-mined SharedLiquidityHook deployment
: "${VAULT:?VAULT must be set to deployed testnet vault address}"
forge script script/DeploySharedLiquidityHook.s.sol --broadcast --rpc-url "$BASE_SEPOLIA_RPC" -vvv

# 3) CREATE2-mined LiquidityAmplifierHook deployment
forge script script/DeployLiquidityAmplifierHook.s.sol --broadcast --rpc-url "$BASE_SEPOLIA_RPC" -vvv

echo
echo "NEXT:"
echo "1. Initialize target pool(s) with deployed hooks."
echo "2. Add seed liquidity + execute test swaps to trigger hook callbacks."
echo "3. Verify fee events and treasury routing."
