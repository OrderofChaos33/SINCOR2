# Sell SINC on the official bonding curve for ETH (off-ramp to fiat separately).
# Usage: .\flip_for_github.ps1 [-SincAmount 120000]
param(
    [int]$SincAmount = 120000
)

$ErrorActionPreference = "Stop"
$cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$rpc = "https://mainnet.base.org"
$NEW = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
$CURVE = "0x75dE341a2BC81806198364F125d4Cde36527619C"
$SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"

$envFile = Join-Path $here ".env"
if (Test-Path $envFile) {
    $line = Get-Content $envFile | Where-Object { $_ -match "^BASE_RPC_URL=(.+)$" } | Select-Object -First 1
    if ($line -match "^BASE_RPC_URL=(.+)$") { $rpc = $matches[1].Trim() }
}

$j = Get-Content (Join-Path $here ".emergency_wallet.json") -Raw | ConvertFrom-Json
$pk = $j.private_key

$sincRaw = [bigint]($SincAmount * [bigint]100000000)
$sincSoldRaw = [bigint]((& $cast call $CURVE "sincSold()(uint256)" --rpc-url $rpc) -split '\s+')[0]
if ($sincRaw -gt $sincSoldRaw) {
    throw "Cannot sell $SincAmount SINC; curve sincSold cap is $($sincSoldRaw / 100000000) SINC"
}

$ethBefore = [bigint](& $cast balance $NEW --rpc-url $rpc)
$refund = ((& $cast call $CURVE "getSellRefund(uint256)(uint256)" $sincRaw.ToString() --rpc-url $rpc) -split '\s+')[0]
Write-Host "Selling $SincAmount SINC -> ~$refund wei ETH (preview)" -ForegroundColor Cyan

Write-Host "Approving curve..." -ForegroundColor Yellow
& $cast send $SINC "approve(address,uint256)" $CURVE $sincRaw.ToString() --rpc-url $rpc --private-key $pk | Out-Null

Write-Host "Selling on curve..." -ForegroundColor Yellow
& $cast send $CURVE "sell(uint256)" $sincRaw.ToString() --rpc-url $rpc --private-key $pk | Out-Null

$ethAfter = [bigint](& $cast balance $NEW --rpc-url $rpc)
$gain = $ethAfter - $ethBefore
Write-Host "Treasury ETH now: $ethAfter wei (+$gain wei)" -ForegroundColor Green
Write-Host "Next: send ETH to Coinbase on Base, sell for USD, pay GitHub billing." -ForegroundColor Cyan