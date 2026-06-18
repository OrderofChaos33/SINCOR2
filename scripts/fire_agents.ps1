# Fire all SINCOR agent cycles once (local ops machine).
$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
$env:PYTHONPATH = "src;$Root"
$env:SWARM_OPS_ENABLED = "true"
$env:LAUNCH_OPS_ENABLED = "true"
$env:CONTENT_AGENT_ENABLED = "true"
$env:DAILY_OPS_ENABLED = "true"
$env:OUTREACH_ENABLED = "true"

Write-Host "=== SINCOR Agent Fire ===" -ForegroundColor Cyan
Write-Host "Root: $Root"

Write-Host "`n[1/6] Swarm ops (6 agents)..." -ForegroundColor Yellow
python scripts/run_swarm_ops.py

Write-Host "`n[2/6] Launch content cycle..." -ForegroundColor Yellow
python -c "import sys; sys.path.insert(0,'.'); from launch_content_engine.run_cycle import run_once; print('drafts:', run_once('campaign_kpi'))"

Write-Host "`n[3/6] Campaign KPI ops..." -ForegroundColor Yellow
python scripts/run_launch_campaign_ops.py

Write-Host "`n[4/6] Launchpad outreach batch..." -ForegroundColor Yellow
python scripts/run_launchpad_outreach.py summary

Write-Host "`n[5/6] Value ops..." -ForegroundColor Yellow
python scripts/run_value_ops.py

Write-Host "`n[6/6] Daily ops..." -ForegroundColor Yellow
python scripts/run_daily_ops.py

Write-Host "`n=== Done. Check /launch/campaign, /launch/launchpads, /launch/review ===" -ForegroundColor Green