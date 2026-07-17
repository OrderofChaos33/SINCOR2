# Verify all 3 mainnet contracts on Basescan
# Requires BASESCAN_API_KEY to be set in onchain/.env
# Run from anywhere; switches to onchain dir internally

$ErrorActionPreference = "Continue"
$root = "C:\Users\cjay4\OneDrive\Desktop\sincor-clean"
$forge = "$env:USERPROFILE\.foundry\bin\forge.exe"
$cast = "$env:USERPROFILE\.foundry\bin\cast.exe"

Set-Location "$root\onchain"

# Pull addresses from deployments
$deploy = Get-Content "deployments\8453.json" -Raw | ConvertFrom-Json
$NFT = $deploy.nft
$CURVE = $deploy.curve
$HOOK = $deploy.hook

# Constants
$SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
$TREASURY = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
$POOL_MANAGER = "0x498581fF718922c3f8e6A244956aF099B2652b2b"
$POSITION_MANAGER = "0x7C5f5A4bBd8fD63184577525326123B519429bDc"

# Check API key is set
$envContent = Get-Content .env
$apiLine = $envContent | Where-Object { $_ -match "^BASESCAN_API_KEY=" }
$apiVal = ($apiLine -replace "^BASESCAN_API_KEY=", "")
if ($apiVal.Length -lt 10) {
    Write-Host "ERROR: BASESCAN_API_KEY not set in .env. Add it first." -ForegroundColor Red
    exit 1
}

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host " VERIFYING 3 CONTRACTS ON BASESCAN" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""

# 1. NFT
Write-Host "[1/3] Verifying SincGenesisNFT at $NFT..." -ForegroundColor Yellow
$nftArgs = & $cast abi-encode "constructor(address)" $CURVE
Write-Host "      Constructor args: $nftArgs"
& $forge verify-contract `
    --chain base `
    --num-of-optimizations 200 `
    --compiler-version 0.8.26 `
    --constructor-args $nftArgs `
    $NFT `
    src/SincGenesisNFT.sol:SincGenesisNFT
Write-Host ""

# 2. Curve
Write-Host "[2/3] Verifying SincBondingCurve at $CURVE..." -ForegroundColor Yellow
$curveArgs = & $cast abi-encode "constructor(address,address,address,address,address)" $SINC $TREASURY $NFT $POOL_MANAGER $POSITION_MANAGER
Write-Host "      Constructor args: $curveArgs"
& $forge verify-contract `
    --chain base `
    --num-of-optimizations 200 `
    --compiler-version 0.8.26 `
    --constructor-args $curveArgs `
    $CURVE `
    src/SincBondingCurve.sol:SincBondingCurve
Write-Host ""

# 3. Hook
Write-Host "[3/3] Verifying SincLimitOrderHook at $HOOK..." -ForegroundColor Yellow
$hookArgs = & $cast abi-encode "constructor(address)" $POOL_MANAGER
Write-Host "      Constructor args: $hookArgs"
& $forge verify-contract `
    --chain base `
    --num-of-optimizations 200 `
    --compiler-version 0.8.26 `
    --constructor-args $hookArgs `
    $HOOK `
    src/SincLimitOrderHook.sol:SincLimitOrderHook
Write-Host ""

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host " VERIFICATION SUBMITTED" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Check status in 30-60 seconds at:"
Write-Host "  NFT:   https://basescan.org/address/$NFT#code"
Write-Host "  Curve: https://basescan.org/address/$CURVE#code"
Write-Host "  Hook:  https://basescan.org/address/$HOOK#code"
Write-Host ""
Write-Host "When you see the green checkmark, re-scan:"
Write-Host "  https://gopluslabs.io/token-security/8453/$SINC"
Write-Host "  https://blocksafu.com/token-scanner/8453/$CURVE"
