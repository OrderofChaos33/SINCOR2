# SINCOR2 Windows local boot fixer
# Run from repo root:  powershell -ExecutionPolicy Bypass -File .\scripts\windows\fix-local-boot.ps1

$ErrorActionPreference = "Continue"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root

Write-Host "SINCOR2 local fix — $Root" -ForegroundColor Cyan

$envFile = Join-Path $Root ".env"
$example = Join-Path $Root ".env.example"
if (-not (Test-Path $envFile)) {
    if (Test-Path $example) {
        Copy-Item $example $envFile
        Write-Host "Created .env from .env.example" -ForegroundColor Yellow
    } else {
        Write-Host "No .env.example found" -ForegroundColor Red
        exit 1
    }
}

function Set-DotEnv {
    param([string]$Key, [string]$Value)
    $lines = Get-Content $envFile
    $found = $false
    $out = foreach ($line in $lines) {
        if ($line -match "^\s*#?\s*$Key=") {
            $found = $true
            "$Key=$Value"
        } else {
            $line
        }
    }
    if (-not $found) { $out += "$Key=$Value" }
    $out | Set-Content -Path $envFile -Encoding utf8
}

# Point data at the repo, not C:\data
Set-DotEnv "SINCOR_DATA_DIR" "./data"
Set-DotEnv "ORDERS_DB_PATH" "./data/orders.db"
Set-DotEnv "DATABASE_URL" "sqlite:///./data/orders.db"
Set-DotEnv "SINCOR_STORE_DB_PATH" "./data/polyclaw.db"
Set-DotEnv "POLYCLAW_DB_PATH" "./data/polyclaw.db"
Set-DotEnv "POLYCLAW_HALT_FILE" "./data/POLYCLAW_HALT"
Set-DotEnv "VAULT_LISTENER_STATE_PATH" "./data/vault_listener_state.json"
Set-DotEnv "WEBBUILDER_DATA_DIR" "./data/webbuilder"

New-Item -ItemType Directory -Force -Path (Join-Path $Root "data") | Out-Null

# Redis: use it if Docker can start it, otherwise sqlite + thread queue
$redisUp = $false
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $iar = $tcp.BeginConnect("127.0.0.1", 6379, $null, $null)
    $ok = $iar.AsyncWaitHandle.WaitOne(400)
    if ($ok -and $tcp.Connected) { $redisUp = $true }
    $tcp.Close()
} catch {}

if (-not $redisUp) {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($docker) {
        Write-Host "Starting redis:7 on 6379 via Docker..." -ForegroundColor Yellow
        docker rm -f sincor2-redis 2>$null | Out-Null
        docker run -d --name sincor2-redis -p 6379:6379 redis:7-alpine | Out-Null
        Start-Sleep -Seconds 2
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            $tcp.Connect("127.0.0.1", 6379)
            $redisUp = $tcp.Connected
            $tcp.Close()
        } catch { $redisUp = $false }
    }
}

if ($redisUp) {
    Set-DotEnv "REDIS_URL" "redis://localhost:6379/0"
    Set-DotEnv "A2A_TASK_STORE" "redis"
    Set-DotEnv "SINCOR_TASK_QUEUE" "auto"
    Write-Host "Redis is up — A2A_TASK_STORE=redis" -ForegroundColor Green
} else {
    Set-DotEnv "A2A_TASK_STORE" "sqlite"
    Set-DotEnv "SINCOR_TASK_QUEUE" "thread"
    # Comment REDIS_URL so the app does not try localhost:6379
    $lines = Get-Content $envFile | ForEach-Object {
        if ($_ -match "^\s*REDIS_URL=") { "# REDIS_URL=redis://localhost:6379/0  # no local redis" } else { $_ }
    }
    $lines | Set-Content -Path $envFile -Encoding utf8
    Write-Host "No Redis — A2A_TASK_STORE=sqlite, SINCOR_TASK_QUEUE=thread" -ForegroundColor Yellow
    Write-Host "Optional: docker run -d --name sincor2-redis -p 6379:6379 redis:7-alpine" -ForegroundColor DarkGray
}

# Keep live-money paths locked on a laptop
Set-DotEnv "POLYCLAW_LIVE" "false"
Set-DotEnv "ALLOW_ONCHAIN_WRITES" "false"
Set-DotEnv "LEGACY_FIAT_PAYMENTS_ENABLED" "false"

$venv = Join-Path $Root ".venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    Write-Host "Creating .venv..." -ForegroundColor Yellow
    python -m venv .venv
}
& ".\.venv\Scripts\python.exe" -m pip install -q -r requirements.txt

Write-Host ""
Write-Host "Fixed. Restart with:" -ForegroundColor Green
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  python run.py"
Write-Host "UI: http://127.0.0.1:8080"
