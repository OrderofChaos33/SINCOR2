# Cancel hook sell orders placed by old Safe -> recover SINC to new treasury (no ETH cost).
# Usage: .\recover_hook_sinc.ps1 dry
#        .\recover_hook_sinc.ps1 go

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("dry", "go")]
    [string]$Mode
)

$ErrorActionPreference = "Stop"
$forge = "$env:USERPROFILE\.foundry\bin\forge.exe"
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

Write-Host "=== Recover SINC from hook sell orders ===" -ForegroundColor Cyan
Write-Host "Recipient: $recipient"
Write-Host ""
Write-Host "Paste OLD Safe private key (0x2d61...daf8) — the wallet that placed hook orders."
Write-Host "MetaMask: the account BEFORE rotation, NOT Account 6 / Af9B."
Write-Host ""

$pk = (Read-Host "Old Safe private key").Trim()
if (-not $pk.StartsWith("0x")) { $pk = "0x$pk" }

$cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
$signer = (& $cast wallet address --private-key $pk).Trim()
$expected = "0x2d61752adF5092052Ff7D366a9884823C07Cdaf8"
if ($signer -ine $expected) {
    Write-Host "Signer is $signer (expected old Safe $expected)" -ForegroundColor Yellow
    Write-Host "If hook orders were placed from Af9B Account 6, use that key instead."
}

$env:SAFE_PRIVATE_KEY = $pk
$env:RECIPIENT = $recipient
Push-Location $here
try {
    $args = @("script", "script/18_RecoverHookSinc.s.sol:RecoverHookSinc", "--rpc-url", $rpc, "-vvv")
    if ($Mode -eq "go") { $args += @("--broadcast", "--slow") }
    & $forge @args
    if ($LASTEXITCODE -ne 0) { throw "forge script failed" }
} finally {
    Pop-Location
    Remove-Item Env:SAFE_PRIVATE_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:RECIPIENT -ErrorAction SilentlyContinue
}

if ($Mode -eq "dry") {
    Write-Host ""
    Write-Host "Dry run OK. Re-run: .\recover_hook_sinc.ps1 go" -ForegroundColor Green
}