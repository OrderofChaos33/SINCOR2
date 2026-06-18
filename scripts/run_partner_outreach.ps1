# Daily partner outreach — sync CRM, write batch file, email reminder.
# Registered by install_ops_schedule.ps1 as "SINCOR Partner Outreach"

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { throw "python not found on PATH" }

Write-Host "[PARTNERS] Sync + summary"
& $py scripts/run_partner_outreach.py sync
& $py scripts/run_partner_outreach.py summary

Write-Host "[PARTNERS] Write today's batch"
& $py scripts/run_partner_outreach.py batch --limit 10

Write-Host "[PARTNERS] Email reminder"
& $py -c "import sys; sys.path.insert(0,'src'); from sincor2.partner_outreach_notify import send_partner_outreach_reminder; import json; print(json.dumps(send_partner_outreach_reminder(), indent=2))"

Write-Host "[PARTNERS] Done — open https://getsincor.com/launch/partners or data/launch_outreach/"