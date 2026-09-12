# SINCOR2 Windows local boot fixer
# NEVER replaces .env. Never touches API keys, admin passwords, or wallet keys.
# Only rewrites Unix /data paths and Redis store mode when those values are still the example defaults.
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\windows\fix-local-boot.ps1

$ErrorActionPreference = "Continue"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root

Write-Host "SINCOR2 local fix — $Root" -ForegroundColor Cyan

$envFile = Join-Path $Root ".env"
$example = Join-Path $Root ".env.example"

if (Test-Path $envFile) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $bak = Join-Path $Root ".env.bak-$stamp"
    Copy-Item $envFile $bak
    Write-Host "Master .env kept. Backup: $bak" -ForegroundColor Green
} else {
    if (Test-Path $example) {
        Copy-Item $example $envFile
        Write-Host "No .env found — copied .env.example (fill secrets yourself)" -ForegroundColor Yellow
    } else {
        Write-Host "No .env and no .env.example" -ForegroundColor Red
        exit 1
    }
}

function Get-DotEnvValue {
    param([string]$Key)
    $line = Get-Content $envFile | Where-Object { $_ -match "^\s*$Key=" } | Select-Object -First 1
    if (-not $line) { return $null }
    return ($line -replace "^\s*$Key=", "")
}

function Set-DotEnvIfDefault {
    param([string]$Key, [string]$Value, [string[]]$OnlyIf)
    $current = Get-DotEnvValue $Key
    if ($null -eq $current) {
        Add-Content $envFile "$Key=$Value"
        Write-Host "Added $Key=$Value" -ForegroundColor DarkGray
        return
    }
    $trim = $current.Trim()
    if ($OnlyIf -and ($OnlyIf -notcontains $trim)) {
        Write-Host "Left $Key alone (not an example default)" -ForegroundColor DarkGray
        return
    }
    $lines = Get-Content $envFile | ForEach-Object {
        if ($_ -match "^\s*$Key=") { "$Key=$Value" } else { $_ }
    }
    $lines | Set-Content -Path $envFile -Encoding utf8
    Write-Host "Set $Key=$Value" -ForegroundColor DarkGray
}

# Only rewrite if still Railway/example Unix paths. Leave a custom data dir alone.
$unixData = @("/data", "\data", "C:\data")
Set-DotEnvIfDefault "SINCOR_DATA_DIR" "./data" $unixData
Set-DotEnvIfDefault "ORDERS_DB_PATH" "./data/orders.db" @("/data/orders.db", "\data\orders.db")
Set-DotEnvIfDefault "DATABASE_URL" "sqlite:///./data/orders.db" @("sqlite:////data/orders.db")
Set-DotEnvIfDefault "SINCOR_STORE_DB_PATH" "./data/polyclaw.db" @("/data/polyclaw.db")
Set-DotEnvIfDefault "POLYCLAW_DB_PATH" "./data/polyclaw.db" @("/data/polyclaw.db")
Set-DotEnvIfDefault "POLYCLAW_HALT_FILE" "./data/POLYCLAW_HALT" @("/data/POLYCLAW_HALT")
Set-DotEnvIfDefault "VAULT_LISTENER_STATE_PATH" "./data/vault_listener_state.json" @("/data/vault_listener_state.json")
Set-DotEnvIfDefault "WEBBUILDER_DATA_DIR" "./data/webbuilder" @("/data/webbuilder")

New-Item -ItemType Directory -Force -Path (Join-Path $Root "data") | Out-Null

$redisUp = $false
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $iar = $tcp.BeginConnect("127.0.0.1", 6379, $null, $null)
    $ok = $iar.AsyncWaitHandle.WaitOne(400)
    if ($ok -and $tcp.Connected) { $redisUp = $true }
    $tcp.Close()
} catch {}

if (-not $redisUp) {
    $store = Get-DotEnvValue "A2A_TASK_STORE"
    if ($store -eq "redis") {
        Set-DotEnvIfDefault "A2A_TASK_STORE" "sqlite" @("redis")
        Write-Host "No local Redis — A2A_TASK_STORE set to sqlite (keys untouched)" -ForegroundColor Yellow
    } else {
        Write-Host "No local Redis — store already $($store)" -ForegroundColor DarkGray
    }
} else {
    Write-Host "Redis is listening on 6379 — left REDIS_URL / A2A_TASK_STORE as-is" -ForegroundColor Green
}

Write-Host ""
Write-Host "Secrets in .env were not replaced." -ForegroundColor Green
Write-Host "Create venv if missing, then:"
Write-Host "  python -m venv .venv"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  pip install -r requirements.txt"
Write-Host "  python run.py"
