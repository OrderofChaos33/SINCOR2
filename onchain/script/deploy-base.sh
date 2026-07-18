#!/usr/bin/env bash
# =============================================================================
# One-click Base mainnet deploy — SINC shared-liquidity + lending stack
#
# Usage (from onchain/):
#   ./script/deploy-base.sh
#
# Prerequisites:
#   - .env in onchain/ containing PRIVATE_KEY=0x...   (never commit this file)
#   - deployer wallet holds a little ETH on Base for gas (~$1 is plenty)
#
# Overrides (optional):
#   BASE_RPC=... DEPLOY_HOOK=0 DEPLOY_LENDING=1 SINC_ORACLE=... SINC_ROUTER=... ./script/deploy-base.sh
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."   # -> onchain/

# --- canonical Base addresses (verified onchain 2026-07-19) ---
export SINC_TOKEN=0x9C8cd8d3961F445D653713dE65C6578bE11668e7   # SINC v3 · 8 dp · 100M supply
export USDC_TOKEN=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913   # native USDC on Base · 6 dp
export POOL_MANAGER=0x498581fF718922c3f8e6A244956aF099B2652b2b # Uniswap V4 PoolManager (all chains)
export TREASURY="${TREASURY:-0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac}"
: "${BASE_RPC:=https://base-rpc.publicnode.com}"

export DEPLOY_HOOK="${DEPLOY_HOOK:-1}"        # SharedLiquidityHook (accounting-layer staging)
export DEPLOY_LENDING="${DEPLOY_LENDING:-0}"  # needs production SINC_ORACLE + SINC_ROUTER first

# --- key handling: PRIVATE_KEY only ever lives in your local .env ---
if [[ -z "${PRIVATE_KEY:-}" && -f .env ]]; then
  set -a; source .env; set +a
fi
[[ -n "${PRIVATE_KEY:-}" ]] || { echo "ERROR: PRIVATE_KEY not set — create onchain/.env"; exit 1; }

# --- pre-flight ---
DEPLOYER=$(cast wallet address "$PRIVATE_KEY")
ETH_WEI=$(cast balance "$DEPLOYER" --rpc-url "$BASE_RPC")
echo "RPC:      $BASE_RPC"
echo "Deployer: $DEPLOYER"
echo "Gas:      $(cast from-wei "$ETH_WEI") ETH on Base"
[[ "$ETH_WEI" -gt 500000000000000 ]] || { echo "ERROR: insufficient ETH on Base for gas"; exit 1; }

# --- deploy ---
forge script script/Deploy.s.sol --broadcast --rpc-url "$BASE_RPC" -vvv

echo
echo "=== NEXT STEPS (see AUDIT.md §4) ==="
echo "1. Mine the production hook address (0xC0 flags) via CREATE2/HookMiner"
echo "2. vault.registerStrategy(hook, backer, capSINC, capUSDC) from guardian"
echo "3. hook.registerPoolStrategy(poolKey, strategyId) from hook owner"
echo "4. Small shadow swap → confirm vault.checkInvariant() == true"
echo "5. Lending: deploy production oracle + router, then rerun with DEPLOY_LENDING=1"
