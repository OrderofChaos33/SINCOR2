# Register all SINCOR Windows scheduled tasks (daemons + content + monitoring).
# Usage:
#   .\scripts\install_ops_schedule.ps1
#   .\scripts\install_ops_schedule.ps1 -BuyWatcher -SkipContentTask   # Railway runs content
#   .\scripts\install_ops_schedule.ps1 -Uninstall

param(
    [switch]$BuyWatcher,
    [switch]$Uninstall,
    [switch]$SkipContentTask,
    [switch]$KeepAwake,
    [int]$ContentHour = 9,
    [int]$ReviewReminderHour = 9,
    [int]$ReviewReminderMinute = 15,
    [int]$PartnerOutreachHour = 8,
    [int]$PartnerOutreachMinute = 30,
    [int]$DailyOpsHour = 8,
    [int]$WeeklyBuyersHour = 10,
    [ValidateSet("Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday")]
    [string]$WeeklyBuyersDay = "Sunday"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $pwsh) { $pwsh = (Get-Command powershell -ErrorAction SilentlyContinue).Source }
if (-not $pwsh) { throw "pwsh or powershell not found on PATH" }

$Tasks = @{
    Daemons         = "SINCOR Launch Daemons"
    Content         = "SINCOR Launch Content"
    ReviewReminder  = "SINCOR Launch Review Reminder"
    PartnerOutreach = "SINCOR Partner Outreach"
    KeepAwake       = "SINCOR Keep Awake"
    DailyOps        = "SINCOR Daily Ops"
    WeeklyBuyers    = "SINCOR Weekly Buyers"
    WhitelistOps    = "SINCOR Whitelist Ops"
}

function Remove-OpsTask([string]$Name) {
    $existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
        Write-Host "Removed: $Name"
    }
}

function Register-OpsTask(
    [string]$Name,
    [string]$Arguments,
    [object]$Trigger,
    [string]$Description
) {
    $action = New-ScheduledTaskAction -Execute $pwsh -Argument $Arguments -WorkingDirectory $root
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger -Settings $settings -Description $Description -Force | Out-Null
    Write-Host "Registered: $Name"
}

if ($Uninstall) {
    foreach ($t in $Tasks.Values) { Remove-OpsTask $t }
    Write-Host "All SINCOR ops tasks removed."
    exit 0
}

# 1) Logon daemons — hook keeper + optional buy watcher
$daemonScript = Join-Path $root "scripts\start_launch_daemons.ps1"
$daemonArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$daemonScript`""
if ($BuyWatcher) { $daemonArgs += " -BuyWatcher" }
Register-OpsTask $Tasks.Daemons $daemonArgs (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) `
    "Hook fill keeper (watch). Skips if already running."

# 2) Daily launch content → review queue (skip if Railway LAUNCH_OPS_ENABLED=true)
if (-not $SkipContentTask) {
    $contentScript = Join-Path $root "scripts\run_launch_ops.ps1"
    $contentArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$contentScript`" -ContentOnly"
    Register-OpsTask $Tasks.Content $contentArgs (New-ScheduledTaskTrigger -Daily -At ("{0:D2}:00" -f $ContentHour)) `
        "Launch content drafts → /launch/review"
} else {
    Remove-OpsTask $Tasks.Content
    Write-Host "Skipped content task (use Railway LAUNCH_OPS_ENABLED=true)"
}

# 3) Daily approval email — ~5 min at /launch/review
$reminderScript = Join-Path $root "scripts\send_launch_review_reminder.py"
$reminderArgs = "-NoProfile -ExecutionPolicy Bypass -Command `"cd '$root'; python '$reminderScript'`""
$remAt = "{0:D2}:{1:D2}" -f $ReviewReminderHour, $ReviewReminderMinute
Register-OpsTask $Tasks.ReviewReminder $reminderArgs (New-ScheduledTaskTrigger -Daily -At $remAt) `
    "Email reminder to approve launch drafts (default: court@getsincor.com)"

# 4) Daily partner outreach — sync CRM, batch copy, email due list
$partnerScript = Join-Path $root "scripts\run_partner_outreach.ps1"
$partnerArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$partnerScript`""
$partnerAt = "{0:D2}:{1:D2}" -f $PartnerOutreachHour, $PartnerOutreachMinute
Register-OpsTask $Tasks.PartnerOutreach $partnerArgs (New-ScheduledTaskTrigger -Daily -At $partnerAt) `
    "Partner pipeline sync + daily outreach batch (July 7 KOLs)"

# 5) Keep machine awake at logon (standby/monitor timeout 0 on AC)
if ($KeepAwake) {
    $awakeScript = Join-Path $root "scripts\keep_machine_awake.ps1"
    $awakeArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$awakeScript`""
    Register-OpsTask $Tasks.KeepAwake $awakeArgs (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) `
        "Disable sleep while plugged in (restore: keep_machine_awake.ps1 -RestoreDefaults)"
} else {
    Remove-OpsTask $Tasks.KeepAwake
}

# 6) Daily read-only ops — chain snapshot, revenue, wallet watch, recover status
$dailyScript = Join-Path $root "scripts\run_daily_ops.ps1"
$dailyArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$dailyScript`" -ChainStatus"
Register-OpsTask $Tasks.DailyOps $dailyArgs (New-ScheduledTaskTrigger -Daily -At ("{0:D2}:00" -f $DailyOpsHour)) `
    "Read-only daily monitoring. Logs in logs/ops/"

# 7) Weekly buyer scan
$weeklyArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$dailyScript`" -Buyers -ChainStatus"
Register-OpsTask $Tasks.WeeklyBuyers $weeklyArgs (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $WeeklyBuyersDay -At ("{0:D2}:00" -f $WeeklyBuyersHour)) `
    "Weekly curve buyer report + chain status"

# 8) Weekly whitelist refresh + listing check
$wlScript = Join-Path $root "scripts\run_whitelist_ops.ps1"
$wlArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$wlScript`""
Register-OpsTask $Tasks.WhitelistOps $wlArgs (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $WeeklyBuyersDay -At ("{0:D2}:30" -f $WeeklyBuyersHour)) `
    "Refresh token list assets + check Balancer/Superchain/etc."

Write-Host ""
Write-Host "=== SINCOR ops schedule ==="
Write-Host "Daemons:      at logon (keeper$(if ($BuyWatcher) { ' + buy watcher' }))"
Write-Host "Daily ops:    $DailyOpsHour`:00 — logs/ops/"
if (-not $SkipContentTask) { Write-Host "Content:      $ContentHour`:00 — /launch/review" }
Write-Host "Review email: $remAt — court@getsincor.com (set LAUNCH_REVIEW_ALERT_EMAIL)"
Write-Host "Partner ops:  $partnerAt — /launch/partners (set PARTNER_OUTREACH_ALERT_EMAIL)"
if ($KeepAwake) { Write-Host "Keep awake:   at logon — standby/monitor off on AC" }
Write-Host "Weekly buyers: $WeeklyBuyersDay $WeeklyBuyersHour`:00"
Write-Host "Whitelist ops: $WeeklyBuyersDay $($WeeklyBuyersHour):30"
Write-Host ""
Write-Host "Railway env (enable what you want):"
Write-Host "  LAUNCH_OPS_ENABLED=true          # content drafts (skip -SkipContentTask locally)"
Write-Host "  DAILY_OPS_ENABLED=true           # chain/revenue/wallet watch"
Write-Host "  COMPLIANCE_MONITOR_ENABLED=true  # marketing/env audit every 30m"
Write-Host "  CONTENT_AGENT_ENABLED=true       # SEO blog agent (48h)"
Write-Host "  OUTREACH_ENABLED=true            # cold email (6h)"
Write-Host "  PARTNER_OUTREACH_ENABLED=true    # daily partner due-list email"
Write-Host ""
Write-Host "List:    Get-ScheduledTask -TaskName 'SINCOR*'"
Write-Host "Remove:  .\scripts\install_ops_schedule.ps1 -Uninstall"