# Refresh SINC token list assets + check whitelist listing status.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Push-Location $root
try {
    python scripts/whitelist_token.py prepare
    python scripts/whitelist_token.py check
} finally {
    Pop-Location
}