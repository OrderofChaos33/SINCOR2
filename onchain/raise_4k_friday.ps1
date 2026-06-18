# $4k USDC by Friday — monitor + share kit (hook floor fills only).
# Needs ~2,667 SINC bought at $1.50/SINC from external USDC buyers.
#
# Usage:
#   .\raise_4k_friday.ps1              # status + share copy
#   .\raise_4k_friday.ps1 -Watch       # poll USDC balance every 5 min
#   .\raise_4k_friday.ps1 -Withdraw    # run fill keeper once

param(
    [switch]$Watch,
    [switch]$Withdraw,
    [int]$IntervalSec = 300
)

$ErrorActionPreference = "Stop"
$Cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
$Rpc = if ($env:BASE_RPC_URL) { $env:BASE_RPC_URL } else { "https://base-rpc.publicnode.com" }
$Treasury = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
$Usdc = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
$Goal = 4000
$SincNeeded = 2667
$Deadline = "Friday 2026-06-19"
$BuyUrl = "https://getsincor.com/sinc#buy-usdc"
$RefUrl = "https://getsincor.com/sinc?ref=$Treasury"

function Get-UsdcBalance {
    $raw = [bigint]((& $Cast call $Usdc "balanceOf(address)(uint256)" $Treasury --rpc-url $Rpc) -split '\s+')[0]
    return [decimal]$raw / 1e6
}

function Show-Status {
    $usdc = Get-UsdcBalance
    $pct = [math]::Min(100, [math]::Round($usdc / $Goal * 100, 1))
    Write-Host ""
    Write-Host "=== `$4k USDC by $Deadline ===" -ForegroundColor Cyan
    Write-Host "Treasury: $Treasury"
    Write-Host "USDC: `$$([math]::Round($usdc, 2)) / `$$Goal ($pct%)"
    Write-Host "Needs: ~$SincNeeded SINC bought at `$1.50/SINC"
    Write-Host ""
    Write-Host "BUY LINK (send to anyone with USDC on Base):" -ForegroundColor Green
    Write-Host "  $BuyUrl"
    Write-Host ""
    Write-Host "REFERRAL (3% ETH on curve buys — secondary):" -ForegroundColor DarkGray
    Write-Host "  $RefUrl"
    Write-Host ""
    Write-Host "SHARE (copy/paste):" -ForegroundColor Yellow
    @"
SINC floor open on Base — `$1.50/SINC minimum, USDC only.
~$SincNeeded SINC (~`$4k) available at floor this week.
CertiK 97 / Sourcify verified / 42-agent platform.
Buy: $BuyUrl
"@ | Write-Host
    if ($usdc -ge $Goal) {
        Write-Host "GOAL MET." -ForegroundColor Green
        return $true
    }
    return $false
}

if ($Withdraw) {
    & (Join-Path $PSScriptRoot "hook_fill_keeper.ps1")
}

if ($Watch) {
    while ($true) {
        if (Show-Status) { break }
        if (-not $Withdraw) {
            & (Join-Path $PSScriptRoot "hook_fill_keeper.ps1") 2>$null
        }
        Start-Sleep -Seconds $IntervalSec
    }
} else {
    Show-Status | Out-Null
    Write-Host "Tip: if YOU have USDC elsewhere, buy from a second wallet at the link above —" -ForegroundColor DarkCyan
    Write-Host "      0x09E receives the USDC when your hook walls fill." -ForegroundColor DarkCyan
    Write-Host "Run: .\raise_4k_friday.ps1 -Watch -Withdraw" -ForegroundColor DarkGray
}