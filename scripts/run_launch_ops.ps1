# SINCOR launch ops daemon — Phase A automation
# Usage:
#   .\scripts\run_launch_ops.ps1              # hook keeper (watch) + content cycle once
#   .\scripts\run_launch_ops.ps1 -BuyWatcher  # also start node buy_watcher.js
#   .\scripts\run_launch_ops.ps1 -ContentOnly # content cycle only

param(
    [switch]$BuyWatcher,
    [switch]$ContentOnly,
    [switch]$KeeperOnly
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$daemonScript = Join-Path $root "scripts\start_launch_daemons.ps1"

if (-not $ContentOnly) {
    Write-Host "=== Launch daemons (keeper + optional buy watcher) ==="
    if (Test-Path $daemonScript) {
        $daemonArgs = @("-NoProfile", "-File", $daemonScript)
        if ($KeeperOnly) { $daemonArgs += "-KeeperOnly" }
        if ($BuyWatcher) { $daemonArgs += "-BuyWatcher" }
        & pwsh @daemonArgs
    } else {
        Write-Warning "Missing $daemonScript"
    }
}

if ($KeeperOnly) { exit 0 }

Write-Host "=== Launch content cycle (review queue) ==="
Push-Location $root
try {
    python launch_content_engine/run_cycle.py --pipeline all
    Write-Host "=== Review reminder email ==="
    python scripts/send_launch_review_reminder.py
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Approve drafts: https://getsincor.com/launch/review  (or http://localhost:5000/launch/review)"
Write-Host "Buy path:       https://getsincor.com/sinc"
Write-Host "Referral:       https://getsincor.com/refer"