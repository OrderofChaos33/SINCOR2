# Launch-week price support: sweep dust ETH -> treasury -> curve buy.
# Usage:
#   .\stabilize_price.ps1 status
#   .\stabilize_price.ps1 buy -Eth 0.01
#   .\stabilize_price.ps1 sweep-and-buy

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("status", "buy", "sweep-and-buy")]
    [string]$Action,
    [string]$Eth = "0.01"
)

$ErrorActionPreference = "Stop"
$cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$rpc = "https://base-mainnet.g.alchemy.com/v2/3oRbUEOinVpgekaPxriSG"
$envFile = Join-Path $here ".env"
if (Test-Path $envFile) {
    $line = Get-Content $envFile | Where-Object { $_ -match "^BASE_RPC_URL=(.+)$" } | Select-Object -First 1
    if ($line -match "^BASE_RPC_URL=(.+)$") { $rpc = $matches[1].Trim() }
}
$oldSafePk = "0x790c1f17217e45fa0fe64e0728dbd7840f69556fc3c3224290733f1b0905c0f4"
$gasReserve = [bigint]80000000000000
$NEW = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
$CURVE = "0x75dE341a2BC81806198364F125d4Cde36527619C"
$SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
$emergency = Join-Path $here ".emergency_wallet.json"
$j = Get-Content $emergency -Raw | ConvertFrom-Json
$treasuryPk = $j.private_key

function Get-Wei([string]$ethDec) {
    return [string][bigint]([math]::Floor([double]$ethDec * 1e18))
}

function Show-Status {
    python -c "import sys,json; sys.path.insert(0,r'$((Split-Path $here -Parent))'); from launch_content_engine.onchain_stats import fetch_stats; print(json.dumps(fetch_stats(), indent=2))"
    Write-Host ""
    Write-Host "To lift curve spot for visitors: fund $NEW then .\stabilize_price.ps1 buy -Eth 0.01" -ForegroundColor Cyan
    Write-Host "Replace incident inventory (~9.83M): .\replenish_value.ps1 buy-curve -SincAtoms 982854705556465 (~0.33 ETH)" -ForegroundColor Yellow
}

function Invoke-CurveBuy([string]$ethWei) {
    $bal = [bigint](& $cast balance --rpc-url $rpc $NEW)
    if ($bal -lt [bigint]$ethWei) {
        throw "Treasury needs $ethWei wei; has $bal wei. Send ETH to $NEW"
    }
    $preview = ((& $cast call --rpc-url $rpc $CURVE "getBuyAmount(uint256)(uint256)" $ethWei) -split '\s+')[0]
    Write-Host "Buying with $ethWei wei -> est $preview SINC atoms"
    & $cast send --rpc-url $rpc --private-key $treasuryPk $CURVE "buy(uint256,address)" $ethWei "0x0000000000000000000000000000000000000000" --value $ethWei
    if ($LASTEXITCODE -ne 0) { throw "buy failed" }
    $after = ((& $cast call --rpc-url $rpc $SINC "balanceOf(address)(uint256)" $NEW) -split '\s+')[0]
    Write-Host "Treasury SINC: $after" -ForegroundColor Green
    python -c "import sys; sys.path.insert(0,r'$((Split-Path $here -Parent))'); from launch_content_engine.onchain_stats import fetch_stats; s=fetch_stats(); print('New spot ~$'+str(s['curve_spot_usd'])+' (curve)')"
}

switch ($Action) {
    "status" { Show-Status }
    "buy" { Invoke-CurveBuy (Get-Wei $Eth) }
    "sweep-and-buy" {
        $j = Get-Content (Join-Path $here ".emergency_wallet.json") -Raw | ConvertFrom-Json
        $pk = $j.private_key
        foreach ($src in @(@{n="treasury"; k=$pk}, @{n="oldSafe"; k=$oldSafePk})) {
            $from = (& $cast wallet address --private-key $src.k).Trim()
            $wei = [bigint](& $cast balance --rpc-url $rpc $from)
            $send = $wei - $gasReserve
            if ($send -gt 0) {
                Write-Host "$($src.n) sweep $send wei"
                & $cast send --rpc-url $rpc --private-key $src.k $NEW --value $send.ToString() | Out-Null
            }
        }
        $bal = [bigint](& $cast balance --rpc-url $rpc $NEW)
        $buy = $bal - $gasReserve
        if ($buy -gt 0) { Invoke-CurveBuy $buy.ToString() }
        Show-Status
    }
}