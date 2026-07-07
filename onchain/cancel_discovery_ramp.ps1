
#$1.50+ floor ladder. Recovered SINC returns to RECIPIENT (default: treasury safe).
#
# Prereq: signer wallet that PLACED the discovery orders (often treasury safe).
#   cd C:\Users\cjay4\.grok\worktrees\desktop-sincor-clean\sincor\onchain
#   $env:PRIVATE_KEY = "<signer that placed discovery rungs>"
#   $env:MAX_FLOOR_RUNGS = "0"   # do NOT cancel floor rungs
#   .\cancel_discovery_ramp.ps1
#
# Dry run first:
#   forge script script/18_RecoverHookSinc.s.sol -vvvv

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not $env:MAX_FLOOR_RUNGS) { $env:MAX_FLOOR_RUNGS = "0" }
if (-not $env:RECIPIENT) { $env:RECIPIENT = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac" }

Write-Host "=== Cancel discovery ramp only (keep `$1.50+ floor) ===" -ForegroundColor Cyan
Write-Host "MAX_FLOOR_RUNGS=$env:MAX_FLOOR_RUNGS (0 = floor rungs untouched)"
Write-Host "RECIPIENT=$env:RECIPIENT"
Write-Host ""

forge script script/18_RecoverHookSinc.s.sol:RecoverHookSinc --rpc-url $env:BASE_RPC_URL --broadcast -vvvv