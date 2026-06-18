# Value creation ops — monetize live assets (no recovery keys needed).
# Usage:
#   .\create_value.ps1 audit
#   .\create_value.ps1 social
#   .\create_value.ps1 hook-keeper
#   .\create_value.ps1 tokenlist-check

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("audit", "social", "hook-keeper", "tokenlist-check")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = "python"
$ops = Join-Path $root "scripts\run_value_ops.py"

function Invoke-ValueOps([string[]]$extra) {
    if (-not (Test-Path $ops)) { throw "Missing $ops" }
    & $py $ops @extra
    if ($LASTEXITCODE -ne 0) { throw "value ops failed" }
}

switch ($Action) {
    "audit" { Invoke-ValueOps @("--audit") }
    "social" { Invoke-ValueOps @("--social") }
    "hook-keeper" {
        $keeper = Join-Path $PSScriptRoot "hook_fill_keeper.ps1"
        & $keeper
    }
    "tokenlist-check" {
        $url = "https://getsincor.com/tokenlists/sincor.tokenlist.json"
        try {
            $j = Invoke-RestMethod -Uri $url -TimeoutSec 20
            Write-Host "Token list OK: $($j.tokens.Count) token(s) at $url" -ForegroundColor Green
        } catch {
            $local = Join-Path $root "static\tokenlists\sincor.tokenlist.json"
            if (Test-Path $local) {
                Write-Host "Remote fetch failed; local file exists: $local" -ForegroundColor Yellow
                Get-Content $local -Raw | ConvertFrom-Json | Format-List name, timestamp
            } else { throw $_ }
        }
    }
}