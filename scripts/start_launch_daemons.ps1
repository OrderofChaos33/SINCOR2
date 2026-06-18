# Start long-running launch daemons (hook keeper + buy watcher) if not already running.
# Used by run_launch_ops.ps1 and the Windows logon scheduled task.

param(
    [switch]$BuyWatcher,
    [switch]$KeeperOnly
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$onchain = Join-Path $root "onchain"
$keeper = Join-Path $onchain "hook_fill_keeper.ps1"
$watcher = Join-Path $root "buy_watcher.js"

function Test-DaemonRunning([string]$Match) {
    Get-CimInstance Win32_Process -Filter "Name = 'pwsh.exe' OR Name = 'powershell.exe' OR Name = 'node.exe'" |
        ForEach-Object { $_.CommandLine } |
        Where-Object { $_ -and $_.Contains($Match) } |
        Select-Object -First 1
}

if (-not $KeeperOnly) {
    if (Test-Path $keeper) {
        if (Test-DaemonRunning "hook_fill_keeper.ps1") {
            Write-Host "Hook fill keeper already running — skip"
        } else {
            Start-Process pwsh -ArgumentList "-NoProfile", "-File", $keeper, "-Watch" -WorkingDirectory $onchain
            Write-Host "Started hook_fill_keeper.ps1 -Watch"
        }
    } else {
        Write-Warning "Missing $keeper"
    }
}

if ($KeeperOnly) { exit 0 }

if ($BuyWatcher -and (Test-Path $watcher)) {
    if (Test-DaemonRunning "buy_watcher.js") {
        Write-Host "Buy watcher already running — skip"
    } else {
        Start-Process node -ArgumentList $watcher -WorkingDirectory $root
        Write-Host "Started buy_watcher.js"
    }
}