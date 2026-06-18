# Create/update repo-root .env for Windows scheduled tasks.
# Secrets: paste from Railway Variables tab (RESEND, Twilio). Never commit .env.
#
# Usage:
#   .\scripts\bootstrap_local_env.ps1
#   .\scripts\bootstrap_local_env.ps1 -FromRailway   # if `railway link` is set up

param(
    [switch]$FromRailway,
    [switch]$SkipContentOnRailway
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$envPath = Join-Path $root ".env"
$example = Join-Path $root ".env.example"

$secretKeys = @(
    "RESEND_API_KEY",
    "TWILIO_ID", "TWILIO_AUTH", "TWILIO_NUMBER",
    "TWILO_ID", "TWILO_AUTH", "TWILO_NUMBER",
    "ANTHROPIC_API_KEY", "BASE_RPC_URL"
)

function Get-EnvMap([string]$Path) {
    $map = @{}
    if (-not (Test-Path $Path)) { return $map }
    Get-Content $Path | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$' -and -not $_.TrimStart().StartsWith('#')) {
            $map[$Matches[1]] = $Matches[2]
        }
    }
    return $map
}

function Set-EnvMap([string]$Path, [hashtable]$Map) {
    $lines = @()
    if (Test-Path $Path) { $lines = Get-Content $Path }
    $out = New-Object System.Collections.Generic.List[string]
    $written = @{}
    foreach ($line in $lines) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=') {
            $k = $Matches[1]
            if ($Map.ContainsKey($k)) {
                $out.Add("$k=$($Map[$k])")
                $written[$k] = $true
                continue
            }
        }
        $out.Add($line)
    }
    foreach ($k in $Map.Keys) {
        if (-not $written.ContainsKey($k)) { $out.Add("$k=$($Map[$k])") }
    }
    $out | Set-Content -Path $Path -Encoding UTF8
}

# Start from example if no .env
if (-not (Test-Path $envPath)) {
    Copy-Item $example $envPath
    Write-Host "Created $envPath from .env.example"
}

$current = Get-EnvMap $envPath
$updates = @{
    LAUNCH_REVIEW_ALERT_EMAIL = "court@getsincor.com"
    LAUNCH_REVIEW_REMINDER_ENABLED = "true"
    LAUNCH_REVIEW_REMINDER_HOUR = "9"
    LAUNCH_REVIEW_REMINDER_MINUTE = "15"
    LAUNCH_REVIEW_REMINDER_AFTER_CYCLE = "true"
    # Windows runs content + daily ops locally — Railway handles if LAUNCH_OPS_ENABLED there
    LAUNCH_OPS_ENABLED = "false"
    DAILY_OPS_ENABLED = "false"
    COMPLIANCE_MONITOR_ENABLED = "false"
    APP_BASE_URL = "https://getsincor.com"
}

if ($FromRailway) {
    $railway = Get-Command railway -ErrorAction SilentlyContinue
    if (-not $railway) { throw "railway CLI not found" }
    Push-Location $root
    try {
        $json = railway variables --json 2>&1
        if ($LASTEXITCODE -ne 0) { throw "railway variables failed: $json" }
        $vars = $json | ConvertFrom-Json
        foreach ($k in $secretKeys) {
            if ($vars.PSObject.Properties.Name -contains $k -and $vars.$k) {
                $updates[$k] = $vars.$k
            }
        }
        # Normalize Twilio naming for buy_watcher.js
        if ($updates.ContainsKey("TWILIO_ID") -and -not $current.TWILIO_SID) { $updates["TWILIO_SID"] = $updates["TWILIO_ID"] }
        if ($updates.ContainsKey("TWILIO_AUTH")) { $updates["TWILIO_AUTH"] = $updates["TWILIO_AUTH"] }
        if ($updates.ContainsKey("TWILIO_NUMBER")) { $updates["TWILIO_FROM"] = $updates["TWILIO_NUMBER"] }
        Write-Host "Pulled secrets from Railway (not printed)."
    } finally {
        Pop-Location
    }
} else {
    Write-Host "Paste these from Railway into .env if empty: RESEND_API_KEY, TWILIO_ID, TWILIO_AUTH, TWILIO_NUMBER"
    Write-Host "Add NOTIFY_PHONE=+1... for buy-watcher SMS to your phone."
}

foreach ($k in $updates.Keys) {
    if (-not $current[$k] -or $current[$k] -eq "") { $current[$k] = $updates[$k] }
    else { $current[$k] = $updates[$k] }  # always set non-secret scheduler keys
}

Set-EnvMap $envPath $current
Write-Host "Updated $envPath"

if ($SkipContentOnRailway) {
    & (Join-Path $root "scripts\install_ops_schedule.ps1") -BuyWatcher -SkipContentTask
    Write-Host "Windows: content skipped locally (use Railway LAUNCH_OPS_ENABLED=true)"
} else {
    Write-Host "Tip: if Railway LAUNCH_OPS_ENABLED=true, run:"
    Write-Host "  .\scripts\install_ops_schedule.ps1 -BuyWatcher -SkipContentTask"
}