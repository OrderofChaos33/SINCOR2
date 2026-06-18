# Alert when MEV bot approves SINC for swap (dump imminent). Balance-only watchers miss this.
param([int]$IntervalSec = 45)
$ErrorActionPreference = "Stop"
$cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$rpc = "https://mainnet.base.org"
$envFile = Join-Path $here ".env"
if (Test-Path $envFile) {
    $line = Get-Content $envFile | Where-Object { $_ -match "^BASE_RPC_URL=(.+)$" } | Select-Object -First 1
    if ($line -match "^BASE_RPC_URL=(.+)$") { $rpc = $matches[1].Trim() }
}
$BOT = "0x66DE38dA216D6fCC3F9Aa944f592546e3eae2dD0"
$SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
$ROUTER = "0x4752ba5DBC23f44d87826276bf6fd6b1C372aD24"
$approveTopic = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
$ownerTopic = "0x000000000000000000000000$($BOT.Substring(2).ToLower())"
$head0 = [uint64](& $cast block-number --rpc-url $rpc)
$from = $head0 - 5
Write-Host "Watching Approval events for bot $BOT (Ctrl+C to stop)"
while ($true) {
    $head = [uint64](& $cast block-number --rpc-url $rpc)
    $logs = & $cast logs --rpc-url $rpc --from-block ($from + 1) --to-block $head --address $SINC $approveTopic $ownerTopic 2>&1
    if ($LASTEXITCODE -eq 0 -and $logs) {
        Write-Host "$(Get-Date -Format o) ALERT: bot approved spender - DUMP MAY BE NEXT" -ForegroundColor Red
        $logs | ForEach-Object { Write-Host $_ }
    }
    $allow = ((& $cast call --rpc-url $rpc $SINC "allowance(address,address)(uint256)" $BOT $ROUTER) -split '\s+')[0]
    if ([decimal]$allow -gt 0) {
        Write-Host "$(Get-Date -Format o) ROUTER ALLOWANCE NONZERO: $allow" -ForegroundColor Red
    } else {
        Write-Host "$(Get-Date -Format o) router allowance=0 (bot still holding, not set up to sell)"
    }
    $from = $head
    Start-Sleep -Seconds $IntervalSec
}