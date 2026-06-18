# Load Neynar keys from naynarsecrets.txt into local .env (never commit .env).
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$EnvFile = Join-Path $Root ".env"
$Defaults = @(
    "$env:USERPROFILE\OneDrive\Desktop\sincor-clean\naynarsecrets.txt",
    (Join-Path $Root "naynarsecrets.txt")
)

$SecretsPath = $args[0]
if (-not $SecretsPath) {
    foreach ($p in $Defaults) {
        if (Test-Path $p) { $SecretsPath = $p; break }
    }
}
if (-not $SecretsPath -or -not (Test-Path $SecretsPath)) {
    Write-Error "naynarsecrets.txt not found. Pass path as first argument."
}

$map = @{}
Get-Content $SecretsPath | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    if ($line -match "^apikey=(.+)$") { $map["NEYNAR_API_KEY"] = $Matches[1].Trim() }
    elseif ($line -match "^wallet\s*i\.?d\.?=(.+)$") { $map["NEYNAR_CLIENT_ID"] = $Matches[1].Trim() }
    elseif ($line -match "^wallet=(0x[a-fA-F0-9]{40})$") { $map["FARCASTER_WALLET"] = $Matches[1].Trim() }
}

if (-not $map["NEYNAR_API_KEY"]) {
    Write-Error "naynarsecrets.txt must include apikey= line"
}

if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Root ".env.example") $EnvFile
}

$content = Get-Content $EnvFile -Raw
foreach ($key in $map.Keys) {
    $val = $map[$key]
    if ($content -match "(?m)^$key=.*$") {
        $content = $content -replace "(?m)^$key=.*$", "$key=$val"
    } else {
        $content += "`n$key=$val"
    }
}
Set-Content -Path $EnvFile -Value $content.TrimEnd() -NoNewline
Write-Host "Synced Neynar env vars to .env (secrets not printed)."
Write-Host "Note: wallet i.d. -> NEYNAR_CLIENT_ID (not FARCASTER_SIGNER_UUID). Run: python scripts/neynar_signer_setup.py"