# SINC token whitelist launcher
# Usage:
#   .\scripts\whitelist_token.ps1 prepare
#   .\scripts\whitelist_token.ps1 check
#   .\scripts\whitelist_token.ps1 launch
#   .\scripts\whitelist_token.ps1 open-forms

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("prepare", "check", "launch", "open-forms")]
    [string]$Action
)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
python (Join-Path $here "whitelist_token.py") $Action
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }