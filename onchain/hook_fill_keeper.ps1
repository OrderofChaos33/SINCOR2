# Monitors SincLimitOrderHook Fill events and withdraws USDC proceeds to the signer wallet.
# Usage:
#   cd onchain
#   .\hook_fill_keeper.ps1              # scan + withdraw new fills once
#   .\hook_fill_keeper.ps1 -Watch       # loop every 5 minutes
# Requires: foundry (cast, forge), onchain/.env with SAFE_PRIVATE_KEY or TREASURY_PRIVATE_KEY

param(
    [switch]$Watch,
    [int]$IntervalSec = 300,
    [uint64]$FromBlock = 0
)

$ErrorActionPreference = "Stop"
$Forge = "$env:USERPROFILE\.foundry\bin\forge.exe"
$Cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
if (-not (Test-Path $Forge)) { throw "forge not found at $Forge" }

$Hook = "0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0"
$Rpc = if ($env:BASE_RPC_URL) { $env:BASE_RPC_URL } else { "https://mainnet.base.org" }
$envFile = Join-Path $PSScriptRoot ".env"
if (-not $env:BASE_RPC_URL -and (Test-Path $envFile)) {
    $line = Get-Content $envFile | Where-Object { $_ -match "^BASE_RPC_URL=(.+)$" } | Select-Object -First 1
    if ($line -match "^BASE_RPC_URL=(.+)$") { $Rpc = $matches[1].Trim() }
}
if (-not $env:SAFE_PRIVATE_KEY -and -not $env:TREASURY_PRIVATE_KEY) {
    $emergency = Join-Path $PSScriptRoot ".emergency_wallet.json"
    if (Test-Path $emergency) {
        $j = Get-Content $emergency -Raw | ConvertFrom-Json
        if ($j.private_key) { $env:SAFE_PRIVATE_KEY = $j.private_key }
    }
}
$StateFile = Join-Path $PSScriptRoot ".hook_keeper_state.json"

# Fill(OrderId indexed orderId, ...)
$FillTopic = & $Cast keccak "Fill(uint232,(address,uint24,int24,address),int24,bool)" 2>$null
if (-not $FillTopic) { $FillTopic = & $Cast sig-event "Fill(uint232,(address,uint24,int24,address),int24,bool)" }

function Get-StartBlock {
    if ($FromBlock -gt 0) { return $FromBlock }
    if (Test-Path $StateFile) {
        $s = Get-Content $StateFile -Raw | ConvertFrom-Json
        if ($s.lastBlock) { return [uint64]$s.lastBlock + 1 }
    }
    # Hook deploy vicinity on Base — adjust if scanning from genesis is slow
    return 28400000
}

function Invoke-Withdraw([string]$OrderId) {
    Write-Host "Withdrawing order $OrderId ..."
    $env:FILL_ORDER_ID = $OrderId
    Push-Location $PSScriptRoot
    try {
        & $Forge script script/13_WithdrawFilledOrders.s.sol --rpc-url $Rpc --broadcast --slow 2>&1
        if ($LASTEXITCODE -ne 0) { Write-Warning "Withdraw failed for order $OrderId (exit $LASTEXITCODE)"; return $false }
        return $true
    } finally {
        Pop-Location
    }
}

function Run-Scan {
    $start = Get-StartBlock
    $head = & $Cast block-number --rpc-url $Rpc
    Write-Host "Scanning Fill events on $Hook from block $start to $head"

    $logs = & $Cast logs --from-block $start --to-block $head --address $Hook $FillTopic --rpc-url $Rpc 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "cast logs failed: $logs"
        return
    }

    $processed = @()
    if (Test-Path $StateFile) {
        $processed = @(Get-Content $StateFile -Raw | ConvertFrom-Json).withdrawnOrderIds
        if (-not $processed) { $processed = @() }
    }

    $lines = @($logs) | Where-Object { $_ -match "^\s*\d+:" -or $_ -match "orderId" -or $_ -match "topics" }
    # Parse JSON log output from cast (newer cast emits JSON array)
    try {
        $parsed = $logs | ConvertFrom-Json
        foreach ($entry in $parsed) {
            $orderId = [bigint]$entry.topics[1]
            $oid = $orderId.ToString()
            if ($processed -contains $oid) { continue }
            if (Invoke-Withdraw $oid) { $processed += $oid }
        }
    } catch {
        Write-Host "No new fills parsed (raw output length: $($logs.Length))"
    }

    @{ lastBlock = [uint64]$head; withdrawnOrderIds = $processed } | ConvertTo-Json | Set-Content $StateFile
    Write-Host "Keeper pass complete. State -> $StateFile"
}

Run-Scan
if ($Watch) {
    while ($true) {
        Start-Sleep -Seconds $IntervalSec
        Run-Scan
    }
}