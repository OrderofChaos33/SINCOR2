# Poll MEV bot wallet for SINC movement (Base)
param([int]$IntervalSec = 120)
$cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
$BOT = "0x66DE38dA216D6fCC3F9Aa944f592546e3eae2dD0"
$SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
$rpc = "https://mainnet.base.org"
$last = $null
Write-Host "Watching $BOT for SINC balance changes (Ctrl+C to stop)"
while ($true) {
    $raw = (& $cast call $SINC "balanceOf(address)(uint256)" $BOT --rpc-url $rpc).Trim()
    $bal = ($raw -split '\s+')[0]
    $ts = Get-Date -Format o
    if ($null -eq $last) { $last = $bal }
    if ($bal -ne $last) {
        Write-Host "$ts ALERT: SINC balance changed $last -> $bal" -ForegroundColor Red
        $last = $bal
    } else {
        Write-Host "$ts SINC=$bal (no change)"
    }
    Start-Sleep -Seconds $IntervalSec
}