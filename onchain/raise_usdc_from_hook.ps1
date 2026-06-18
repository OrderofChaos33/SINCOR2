# Raise USDC by placing $1.50 hook sell walls from 0x09E treasury SINC.
# CoW/Kyber have no SINC liquidity on Base — hook fills are the only on-protocol path at floor price.
#
# Targets (defaults): ~$4k immediate tranche, +$20k tranche, +$50k cap tranche when filled.
# USDC lands in treasury when buyers hit getsincor.com/sinc USDC buy (or any hook router buy).
#
# Usage:
#   cd onchain
#   .\raise_usdc_from_hook.ps1 -DryRun
#   .\raise_usdc_from_hook.ps1 -PlaceLadder
#   .\raise_usdc_from_hook.ps1 -WatchFills

param(
    [switch]$DryRun,
    [switch]$PlaceLadder,
    [switch]$WatchFills,
    [switch]$Inspect
)

$ErrorActionPreference = "Stop"
$Forge = "$env:USERPROFILE\.foundry\bin\forge.exe"
$Cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$Rpc = if ($env:BASE_RPC_URL) { $env:BASE_RPC_URL } else { "https://base-rpc.publicnode.com" }
$Treasury = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
$Sinc = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
$Usdc = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

if (-not $env:SAFE_PRIVATE_KEY -and -not $env:TREASURY_PRIVATE_KEY -and -not $env:PRIVATE_KEY) {
    $emergency = Join-Path $root ".emergency_wallet.json"
    if (Test-Path $emergency) {
        $j = Get-Content $emergency -Raw | ConvertFrom-Json
        if ($j.private_key) { $env:SAFE_PRIVATE_KEY = $j.private_key }
    }
}
if (-not $env:SAFE_PRIVATE_KEY -and -not $env:TREASURY_PRIVATE_KEY -and -not $env:PRIVATE_KEY) {
    throw "Set SAFE_PRIVATE_KEY / TREASURY_PRIVATE_KEY / PRIVATE_KEY for $Treasury"
}

Write-Host "=== USDC raise via hook floor (not CoW — no SINC liquidity on aggregators) ===" -ForegroundColor Cyan
$sincBal = [decimal]((& $Cast call $Sinc "balanceOf(address)(uint256)" $Treasury --rpc-url $Rpc) -split '\s+')[0] / 1e8
$usdcBal = [decimal]((& $Cast call $Usdc "balanceOf(address)(uint256)" $Treasury --rpc-url $Rpc) -split '\s+')[0] / 1e6
Write-Host "Treasury SINC: $([math]::Round($sincBal, 0)) | USDC: `$$([math]::Round($usdcBal, 2))"
Write-Host "Tranche targets: ~`$4k (2,667 SINC) + ~`$20k (13,333 SINC) + ~`$50k cap (33,334 SINC) at `$1.50+"
Write-Host ""

if ($Inspect -or (-not $PlaceLadder -and -not $WatchFills)) {
    & $Forge script script/19_InspectAllHookOrders.s.sol --rpc-url $Rpc -vv
}

if ($PlaceLadder) {
    if ($DryRun) {
        & $Forge script script/21_PlaceUsdcRaiseLadder.s.sol --rpc-url $Rpc -vvvv
    } else {
        & $Forge script script/21_PlaceUsdcRaiseLadder.s.sol --rpc-url $Rpc --broadcast -vvvv
        if ($LASTEXITCODE -ne 0) { throw "Ladder placement failed" }
        Write-Host "Ladder placed. Share https://getsincor.com/sinc#buy-usdc to attract USDC buyers." -ForegroundColor Green
    }
}

if ($WatchFills) {
    if ($DryRun) {
        Write-Host "Dry run: would start hook_fill_keeper.ps1 -Watch" -ForegroundColor Yellow
    } else {
        & (Join-Path $root "hook_fill_keeper.ps1") -Watch
    }
}