# Prevent Windows sleep while SINCOR launch ops run locally.
# Run at logon via install_ops_schedule.ps1 -KeepAwake, or manually:
#   pwsh -File scripts/keep_machine_awake.ps1
# Stop: powercfg /change standby-timeout-ac 30  (restore defaults)

param(
    [switch]$RestoreDefaults,
    [int]$StandbyMinutesAC = 0,
    [int]$StandbyMinutesDC = 0,
    [int]$MonitorMinutesAC = 0
)

$ErrorActionPreference = "Stop"

if ($RestoreDefaults) {
    powercfg /change standby-timeout-ac 30
    powercfg /change standby-timeout-dc 15
    powercfg /change monitor-timeout-ac 10
    Write-Host "Power plan restored (AC standby 30m, monitor 10m)"
    exit 0
}

# AC = plugged in; DC = battery
powercfg /change standby-timeout-ac $StandbyMinutesAC
powercfg /change standby-timeout-dc $StandbyMinutesDC
powercfg /change monitor-timeout-ac $MonitorMinutesAC
powercfg /change disk-timeout-ac 0

Write-Host "Keep-awake active: standby AC=${StandbyMinutesAC}m DC=${StandbyMinutesDC}m monitor AC=${MonitorMinutesAC}m"
Write-Host "Restore later: pwsh -File scripts/keep_machine_awake.ps1 -RestoreDefaults"