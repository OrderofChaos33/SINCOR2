# Back-compat wrapper — installs full ops schedule via install_ops_schedule.ps1
param(
    [switch]$BuyWatcher,
    [switch]$Uninstall,
    [int]$ContentHour = 9
)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$installer = Join-Path $here "install_ops_schedule.ps1"
$invokeArgs = @("-NoProfile", "-File", $installer)
if ($BuyWatcher) { $invokeArgs += "-BuyWatcher" }
if ($Uninstall) { $invokeArgs += "-Uninstall" }
$invokeArgs += @("-ContentHour", $ContentHour)
& pwsh @invokeArgs