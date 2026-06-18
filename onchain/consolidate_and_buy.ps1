# Sweep all recoverable ETH to new treasury and buy max SINC from curve.
$ErrorActionPreference = "Stop"
$cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
$rpc = "https://mainnet.base.org"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$NEW = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
$CURVE = "0x75dE341a2BC81806198364F125d4Cde36527619C"
$SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"

$j = Get-Content (Join-Path $here ".emergency_wallet.json") -Raw | ConvertFrom-Json
$newPk = $j.private_key

$envFile = Join-Path $here ".env"
if (Test-Path $envFile) {
    $line = Get-Content $envFile | Where-Object { $_ -match "^BASE_RPC_URL=(.+)$" } | Select-Object -First 1
    if ($line -match "^BASE_RPC_URL=(.+)$") { $rpc = $matches[1].Trim() }
}

$balWei = [bigint](& $cast balance $NEW --rpc-url $rpc)
$buyWei = $balWei - 200000000000000
if ($buyWei -le 0) { Write-Host "Not enough ETH on $NEW for curve buy"; exit 0 }

$preview = ((& $cast call $CURVE "getBuyAmount(uint256)(uint256)" $buyWei.ToString() --rpc-url $rpc) -split '\s+')[0]
Write-Host "Curve buy $buyWei wei -> ~$preview SINC atoms"
& $cast send $CURVE "buy(uint256,address)" $buyWei.ToString() "0x0000000000000000000000000000000000000000" --value $buyWei.ToString() --rpc-url $rpc --private-key $newPk
$final = ((& $cast call $SINC "balanceOf(address)(uint256)" $NEW --rpc-url $rpc) -split '\s+')[0]
Write-Host "Treasury SINC: $final" -ForegroundColor Green