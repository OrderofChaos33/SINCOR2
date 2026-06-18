# Burn rogue Uniswap V2 LP and recover SINC on Af9B treasury.
# Sends both txs back-to-back (no delay) to reduce MEV sniping on burn().
# Usage: .\rogue_v2_exit_interactive.ps1

$ErrorActionPreference = "Stop"
$cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
if (-not (Test-Path $cast)) { throw "cast not found at $cast - install Foundry first" }

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$envFile = Join-Path $here ".env"
$rpc = "https://mainnet.base.org"
if (Test-Path $envFile) {
    $line = Get-Content $envFile | Where-Object { $_ -match "^BASE_RPC_URL=(.+)$" } | Select-Object -First 1
    if ($line -match "^BASE_RPC_URL=(.+)$") { $rpc = $matches[1].Trim() }
}

$PAIR = "0x85372932f9b151a076815d92cf71a97980ffd667"
$AF9B = "0xAf9B539D8043C634b7E611818518BA7E850F289e"

Write-Host ""
Write-Host "=== Rogue V2 LP exit ===" -ForegroundColor Cyan
Write-Host "Wallet: $AF9B"
Write-Host "Pair:   $PAIR"
Write-Host ""
Write-Host "MetaMask Account 6 -> Show private key -> paste below."
Write-Host "(Plain paste - not hidden - so Ctrl+V works reliably.)"
Write-Host ""

$pk = (Read-Host "Af9B private key").Trim()
if (-not $pk) { throw "No key entered" }
if (-not $pk.StartsWith("0x")) { $pk = "0x$pk" }

$signer = (& $cast wallet address --private-key $pk).Trim()
Write-Host "Signer: $signer"
if ($signer -ine $AF9B) {
    throw "Wrong key: $signer (need Account 6 / $AF9B)"
}

$lpRaw = (& $cast call $PAIR "balanceOf(address)(uint256)" $AF9B --rpc-url $rpc).Trim()
$lp = ($lpRaw -split '\s+')[0]
if ([decimal]$lp -le 0) {
    throw @"
Af9B holds 0 LP on this pair already.
If you already sent tx1 (transfer LP), a bot may have sniped burn() - check:
  https://base.blockscout.com/address/$AF9B
Do NOT run again unless LP balance is back above 0.
"@
}

Write-Host "LP to burn: $lp"
Write-Host "Sending tx 1/2 + 2/2 immediately (anti-MEV) ..."

$gasArgs = @("--priority-gas-price", "100000000")  # 0.1 gwei tip on Base

& $cast send $PAIR "transfer(address,uint256)" $PAIR $lp --rpc-url $rpc --private-key $pk @gasArgs
if ($LASTEXITCODE -ne 0) { throw "Tx 1 failed - paste full error above" }

& $cast send $PAIR "burn(address)" $AF9B --rpc-url $rpc --private-key $pk @gasArgs
if ($LASTEXITCODE -ne 0) {
    throw @"
Tx 2 FAILED - LP may be sitting on the pair unburned. A bot can steal the SINC.
Open BaseScan NOW and send burn() manually from Account 6:
  To:   $PAIR
  Data: 0x89afcb44000000000000000000000000af9b539d8043c634b7e611818518ba7e850f289e
"@
}

$balRaw = (& $cast call "0x9C8cd8d3961F445D653713dE65C6578bE11668e7" "balanceOf(address)(uint256)" $AF9B --rpc-url $rpc).Trim()
$bal = ($balRaw -split '\s+')[0]
Write-Host ""
Write-Host "Done. Af9B SINC balance (raw): $bal" -ForegroundColor Green