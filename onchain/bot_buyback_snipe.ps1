# If MEV bot dumps SINC to rogue V2 pair, buy it back with USDC via Uniswap V2 router.
# Usage: .\bot_buyback_snipe.ps1 -Usdc 50
# Requires USDC + ETH on NEW treasury; uses key from .emergency_wallet.json only.

param(
    [decimal]$Usdc = 10,
    [string]$PrivateKey = ""
)

$ErrorActionPreference = "Stop"
$cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$rpc = "https://mainnet.base.org"
$envFile = Join-Path $here ".env"
if (Test-Path $envFile) {
    $line = Get-Content $envFile | Where-Object { $_ -match "^BASE_RPC_URL=(.+)$" } | Select-Object -First 1
    if ($line -match "^BASE_RPC_URL=(.+)$") { $rpc = $matches[1].Trim() }
}

$ROUTER = "0x4752ba5DBC23f44d87826276bf6fd6b1C372aD24"
$PAIR = "0x85372932f9b151a076815d92cf71a97980ffd667"
$SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
$USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
$TREASURY = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
$BOT = "0x66DE38dA216D6fCC3F9Aa944f592546e3eae2dD0"

function Load-Key() {
    if ($PrivateKey) { return $(if ($PrivateKey.StartsWith("0x")) { $PrivateKey } else { "0x$PrivateKey" }) }
    $j = Get-Content (Join-Path $here ".emergency_wallet.json") -Raw | ConvertFrom-Json
    return $j.private_key
}

$botBefore = (($cast call $SINC "balanceOf(address)(uint256)" $BOT --rpc-url $rpc) -split '\s+')[0]
$res = @(& $cast call $PAIR "getReserves()(uint112,uint112,uint32)" --rpc-url $rpc)
$sincReserve = ($res[1] -split '\s+')[0]
Write-Host "Bot SINC: $botBefore | Pair SINC reserve: $sincReserve"
if ([decimal]$sincReserve -lt 1000000000000) {
    Write-Host "Rogue pair has little SINC — wait for bot dump or use replenish_value.ps1 buy-curve instead."
}

$pk = Load-Key
$usdcAtoms = [string][bigint]([math]::Floor($Usdc * 1000000))
$deadline = [string]([bigint](& $cast block latest --rpc-url $rpc -f timestamp) + 600)

Write-Host "Approving USDC..."
& $cast send $USDC "approve(address,uint256)" $ROUTER $usdcAtoms --rpc-url $rpc --private-key $pk

# swapExactTokensForTokens(amountIn, amountOutMin, path, to, deadline)
# token0=USDC token1=SINC on pair
$amountOutMin = 0
Write-Host "Swapping $usdcAtoms USDC atoms for SINC via rogue pair (bot-dump buyback)..."
& $cast send $ROUTER "swapExactTokensForTokens(uint256,uint256,address[],address,uint256)" $usdcAtoms $amountOutMin "[$USDC,$SINC]" $TREASURY $deadline --rpc-url $rpc --private-key $pk

$treasurySinc = (($cast call $SINC "balanceOf(address)(uint256)" $TREASURY --rpc-url $rpc) -split '\s+')[0]
Write-Host "Treasury SINC atoms: $treasurySinc" -ForegroundColor Green