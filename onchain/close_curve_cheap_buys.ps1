# Remove public sub-floor buys by sweeping bonding-curve SINC inventory into treasury.
# The deployed curve has NO pause — buying out inventory is the only way to close cheap ETH buys.
#
# Prereq: treasury wallet with enough ETH on Base (estimate ~$5-8k for full ~65M SINC at micro-spot).
#   cd onchain
#   $env:PRIVATE_KEY = "<treasury safe key>"
#   $env:BASE_RPC_URL = "https://base-rpc.publicnode.com"
#   .\close_curve_cheap_buys.ps1 -DryRun
#   .\close_curve_cheap_buys.ps1

param(
    [switch]$DryRun,
    [string]$Recipient = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
)

$ErrorActionPreference = "Stop"
$cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$CURVE = "0x75dE341a2BC81806198364F125d4Cde36527619C"
$SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
$rpc = if ($env:BASE_RPC_URL) { $env:BASE_RPC_URL } else { "https://base-rpc.publicnode.com" }
$gasReserve = [bigint]5000000000000000  # 0.005 ETH

if (-not $env:PRIVATE_KEY) { throw "Set PRIVATE_KEY to treasury signer" }

$signer = (& $cast wallet address --private-key $env:PRIVATE_KEY).Trim()
$ethBal = [bigint](& $cast balance $signer --rpc-url $rpc)
$curveSinc = [bigint]((& $cast call $SINC "balanceOf(address)(uint256)" $CURVE --rpc-url $rpc) -split '\s+')[0]
$priceWei = [bigint]((& $cast call $CURVE "currentPriceWei()(uint256)" --rpc-url $rpc) -split '\s+')[0]
$spotUsd = [decimal]$priceWei / 1e18 * 3000

Write-Host "=== Close bonding-curve cheap buys ===" -ForegroundColor Cyan
Write-Host "Signer: $signer"
Write-Host "Curve SINC inventory: $curveSinc atoms (~$([math]::Round([decimal]$curveSinc/1e8/1e6, 2))M)"
Write-Host "Curve micro-spot: ~`$$([math]::Round($spotUsd, 8))/SINC (NOT official price)"
Write-Host "Recipient: $Recipient"
Write-Host ""

if ($curveSinc -le 0) {
    Write-Host "Curve inventory already empty — nothing to sweep." -ForegroundColor Green
    exit 0
}

$buyWei = $ethBal - $gasReserve
if ($buyWei -le 0) { throw "Insufficient ETH on signer (need > 0.005 ETH reserve)" }

$previewSinc = [bigint]((& $cast call $CURVE "getBuyAmount(uint256)(uint256)" $buyWei.ToString() --rpc-url $rpc) -split '\s+')[0]
Write-Host "One-shot buy with available ETH:"
Write-Host "  ETH in: $buyWei wei (~$([math]::Round([decimal]$buyWei/1e18, 4)) ETH)"
Write-Host "  SINC out: ~$previewSinc atoms (~$([math]::Round([decimal]$previewSinc/1e8/1e6, 2))M)"

if ($DryRun) {
    Write-Host "Dry run — no tx sent. Re-run without -DryRun to sweep." -ForegroundColor Yellow
    exit 0
}

& $cast send $CURVE "buy(uint256,address)" $buyWei.ToString() "0x0000000000000000000000000000000000000000" `
    --value $buyWei.ToString() --rpc-url $rpc --private-key $env:PRIVATE_KEY

$left = [bigint]((& $cast call $SINC "balanceOf(address)(uint256)" $CURVE --rpc-url $rpc) -split '\s+')[0]
Write-Host "Curve SINC remaining: $left atoms" -ForegroundColor Green
if ($left -gt 0) {
    Write-Host "Re-run after funding more ETH until curve inventory is depleted." -ForegroundColor Yellow
}