# Bonding curve -> ETH -> USDC (Kyber). Honest limits documented below.
#
# The ~65M SINC in the curve CONTRACT is buyer inventory — NOT yours to swap.
# sell() only works for SINC previously bought from the curve (<= sincSold cap).
#
# Usage:
#   .\curve_to_usdc.ps1 -DryRun
#   .\curve_to_usdc.ps1 -SellAllAllowed   # max sincSold back to curve -> ETH -> USDC
#   .\curve_to_usdc.ps1 -SellSinc 50000    # specific amount (must be <= sincSold)

param(
    [switch]$DryRun,
    [switch]$SellAllAllowed,
    [int]$SellSinc = 0
)

$ErrorActionPreference = "Stop"
$Cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Rpc = if ($env:BASE_RPC_URL) { $env:BASE_RPC_URL } else { "https://base-rpc.publicnode.com" }
$Treasury = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
$Curve = "0x75dE341a2BC81806198364F125d4Cde36527619C"
$Sinc = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
$Usdc = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
$Weth = "0x4200000000000000000000000000000000000006"
$Kyber = "0x6131B5fae19EA4f9D964eAc0408E4408b66337b5"

if (-not $env:PRIVATE_KEY) {
    $emergency = Join-Path $here ".emergency_wallet.json"
    if (Test-Path $emergency) {
        $j = Get-Content $emergency -Raw | ConvertFrom-Json
        if ($j.private_key) { $env:PRIVATE_KEY = $j.private_key }
    }
}
if (-not $env:PRIVATE_KEY) { throw "Set PRIVATE_KEY for $Treasury" }

function Get-Atoms([string]$raw) { return [bigint](($raw -split '\s+')[0]) }

$sincSold = Get-Atoms (& $Cast call $Curve "sincSold()(uint256)" --rpc-url $Rpc)
$curveSinc = Get-Atoms (& $Cast call $Sinc "balanceOf(address)(uint256)" $Curve --rpc-url $Rpc)
$ethAcc = Get-Atoms (& $Cast call $Curve "ethAccumulated()(uint256)" --rpc-url $Rpc)

Write-Host "=== Curve -> USDC reality check ===" -ForegroundColor Cyan
Write-Host "SINC in curve contract (buyer inventory, NOT withdrawable): $([decimal]$curveSinc/1e8/1e6)M"
Write-Host "ETH locked in curve: $([decimal]$ethAcc/1e18) ETH (~`$$([math]::Round([decimal]$ethAcc/1e18*3000,2)))"
Write-Host "sincSold (max you can sell BACK to curve): $([decimal]$sincSold/1e8) SINC"
Write-Host ""

if ($SellAllAllowed) { $SellSinc = [int]([decimal]$sincSold / 1e8) }
if ($SellSinc -le 0) {
    Write-Host "Specify -SellAllAllowed or -SellSinc <amount>" -ForegroundColor Yellow
    Write-Host "For `$4k+ USDC at `$1.50: curve cannot do it. Use hook fills or find USDC buyers." -ForegroundColor Yellow
    exit 0
}

$sincAtoms = [bigint]($SellSinc * [bigint]100000000)
if ($sincAtoms -gt $sincSold) { throw "Cannot sell $SellSinc SINC; sincSold cap is $([decimal]$sincSold/1e8)" }

$refundWei = Get-Atoms (& $Cast call $Curve "getSellRefund(uint256)(uint256)" $sincAtoms.ToString() --rpc-url $Rpc)
$refundEth = [decimal]$refundWei / 1e18
Write-Host "Sell $SellSinc SINC on curve -> ~$refundEth ETH (~`$$([math]::Round($refundEth*3000,2)))" -ForegroundColor Yellow
Write-Host "This is micro-spot, NOT `$1.50/SINC." -ForegroundColor Red

if ($DryRun) {
    Write-Host "Dry run — no transactions." -ForegroundColor Yellow
    exit 0
}

$pk = $env:PRIVATE_KEY
& $Cast send $Sinc "approve(address,uint256)" $Curve $sincAtoms.ToString() --rpc-url $Rpc --private-key $pk | Out-Null
& $Cast send $Curve "sell(uint256)" $sincAtoms.ToString() --rpc-url $Rpc --private-key $pk | Out-Null
Write-Host "Curve sell done." -ForegroundColor Green

$ethBal = Get-Atoms (& $Cast balance $Treasury --rpc-url $Rpc)
$swapWei = $ethBal - [bigint]2000000000000000
if ($swapWei -le 0) { Write-Host "Not enough ETH left to swap after gas reserve."; exit 0 }

# Kyber quote ETH -> USDC
$quoteUrl = "https://aggregator-api.kyberswap.com/base/api/v1/routes?tokenIn=$Weth&tokenOut=$Usdc&amountIn=$swapWei&to=$Treasury&saveGas=0"
$route = Invoke-RestMethod -Uri $quoteUrl -Headers @{ "User-Agent" = "sincor/1" }
$summary = $route.data.routeSummary
$buildBody = @{
    routeSummary = $summary
    sender       = $Treasury
    recipient    = $Treasury
    slippageTolerance = 300
} | ConvertTo-Json -Depth 10
$built = Invoke-RestMethod -Method Post -Uri "https://aggregator-api.kyberswap.com/base/api/v1/route/build" -Body $buildBody -ContentType "application/json"
$router = $built.data.routerAddress
$calldata = $built.data.data

& $Cast send $Weth "deposit()" --value $swapWei.ToString() --rpc-url $Rpc --private-key $pk | Out-Null
& $Cast send $Weth "approve(address,uint256)" $router $swapWei.ToString() --rpc-url $Rpc --private-key $pk | Out-Null
& $Cast send $router $calldata --rpc-url $Rpc --private-key $pk | Out-Null

$usdcBal = Get-Atoms (& $Cast call $Usdc "balanceOf(address)(uint256)" $Treasury --rpc-url $Rpc)
Write-Host "Treasury USDC: $([decimal]$usdcBal/1e6)" -ForegroundColor Green