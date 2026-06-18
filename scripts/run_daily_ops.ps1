# Safe daily ops — read-only chain/revenue/watch (+ optional on-chain status on Windows).
param(
    [switch]$Buyers,
    [switch]$ChainStatus
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$logDir = Join-Path $root "logs\ops"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("daily_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmm"))

function Write-Log([string]$Msg) {
    $line = "{0} {1}" -f (Get-Date -Format o), $Msg
    Add-Content -Path $logFile -Value $line
    Write-Host $line
}

Write-Log "=== SINCOR daily ops ==="
Push-Location $root
try {
    $pyArgs = @("scripts/run_daily_ops.py")
    if ($Buyers) { $pyArgs += "--buyers" }
    python @pyArgs 2>&1 | Tee-Object -FilePath $logFile -Append
    if ($LASTEXITCODE -ne 0) { Write-Log "WARN: run_daily_ops.py exit $LASTEXITCODE" }

    if ($ChainStatus) {
        $recover = Join-Path $root "onchain\recover_liquidity.ps1"
        if (Test-Path $recover) {
            Write-Log "=== recover_liquidity status ==="
            & pwsh -NoProfile -File $recover status 2>&1 | Tee-Object -FilePath $logFile -Append
        }
    }
} finally {
    Pop-Location
}
Write-Log "Done. Log: $logFile"