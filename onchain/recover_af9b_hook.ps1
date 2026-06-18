# Cancel hook floor orders placed by Af9B / MetaMask Account 6 -> recover ~20M SINC to new treasury.
# Usage: .\recover_af9b_hook.ps1 inspect
#        .\recover_af9b_hook.ps1 dry
#        .\recover_af9b_hook.ps1 go
#        .\recover_af9b_hook.ps1 go -MaxFloorRungs 3

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("inspect", "dry", "go")]
    [string]$Mode,

    [int]$MaxFloorRungs = 0,
    [string]$PrivateKey = ""
)

$ErrorActionPreference = "Stop"
$forge = "$env:USERPROFILE\.foundry\bin\forge.exe"
$cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$rpc = "https://mainnet.base.org"
$envFile = Join-Path $here ".env"
if (Test-Path $envFile) {
    $line = Get-Content $envFile | Where-Object { $_ -match "^BASE_RPC_URL=(.+)$" } | Select-Object -First 1
    if ($line -match "^BASE_RPC_URL=(.+)$") { $rpc = $matches[1].Trim() }
}

$emergency = Join-Path $here ".emergency_wallet.json"
$j = Get-Content $emergency -Raw | ConvertFrom-Json
$recipient = $j.new_treasury
$AF9B = "0xAf9B539D8043C634b7E611818518BA7E850F289e"

Push-Location $here
try {
    if ($Mode -eq "inspect") {
        Write-Host "=== Hook orders for OLD safe + Af9B ===" -ForegroundColor Cyan
        & $forge script script/19_InspectAllHookOrders.s.sol:InspectAllHookOrders --rpc-url $rpc -vv
        if ($LASTEXITCODE -ne 0) { throw "inspect failed" }
        exit 0
    }

    Write-Host "=== Recover SINC from Af9B hook floor orders ===" -ForegroundColor Cyan
    Write-Host "Recipient: $recipient"
    Write-Host "Expected signer: $AF9B (MetaMask Account 6)"
    Write-Host ""
    if ($PrivateKey) {
        $pk = $PrivateKey.Trim()
    } else {
        Write-Host "Paste Account 6 private key (the wallet that placed floor hook orders)."
        Write-Host "Never paste this key in chat - only here locally."
        Write-Host ""
        $pk = (Read-Host "Account 6 private key").Trim()
    }
    if (-not $pk.StartsWith("0x")) { $pk = "0x$pk" }

    $signer = (& $cast wallet address --private-key $pk).Trim()
    if ($signer -ine $AF9B) {
        Write-Host "Signer is $signer (expected Af9B $AF9B)" -ForegroundColor Yellow
        $ok = Read-Host "Continue anyway? (y/N)"
        if ($ok -ne "y") { exit 1 }
    }

    $env:SAFE_PRIVATE_KEY = $pk
    $env:RECIPIENT = $recipient
    $env:SKIP_DISCOVERY = "true"
    if ($MaxFloorRungs -gt 0) { $env:MAX_FLOOR_RUNGS = [string]$MaxFloorRungs }

    $args = @("script", "script/18_RecoverHookSinc.s.sol:RecoverHookSinc", "--rpc-url", $rpc, "-vvv")
    if ($Mode -eq "go") { $args += @("--broadcast", "--slow") }
    & $forge @args
    if ($LASTEXITCODE -ne 0) { throw "forge script failed" }
} finally {
    Pop-Location
    Remove-Item Env:SAFE_PRIVATE_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:RECIPIENT -ErrorAction SilentlyContinue
    Remove-Item Env:SKIP_DISCOVERY -ErrorAction SilentlyContinue
    Remove-Item Env:MAX_FLOOR_RUNGS -ErrorAction SilentlyContinue
}

if ($Mode -eq "dry") {
    Write-Host ""
    Write-Host "Dry run OK. Broadcast: .\recover_af9b_hook.ps1 go" -ForegroundColor Green
}