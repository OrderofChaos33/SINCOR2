# Push SINCOR branch to GitHub (uses GITHUB_TOKEN env or github_pat.txt — never stores PAT in git config).
$ErrorActionPreference = "Stop"
$Root = "C:\Users\cjay4\.grok\worktrees\desktop-sincor-clean\sincor"
$Branch = if ($args[0]) { $args[0] } else { "launch/kpi-campaign-2026" }

Set-Location $Root

# Repair OneDrive git damage before push
if (Test-Path "scripts\git_repair.ps1") {
    & "$Root\scripts\git_repair.ps1"
}

$token = $env:GITHUB_TOKEN
if (-not $token) { $token = $env:GH_TOKEN }
if (-not $token -and (Test-Path "$env:USERPROFILE\.secrets\sincor\github_pat.txt")) {
    $token = (Get-Content "$env:USERPROFILE\.secrets\sincor\github_pat.txt" -Raw).Trim()
}
if (-not $token -and (Test-Path "$env:USERPROFILE\OneDrive\Desktop\github_pat.txt")) {
    $token = (Get-Content "$env:USERPROFILE\OneDrive\Desktop\github_pat.txt" -Raw).Trim()
}
if (-not $token) {
    Write-Host "No GITHUB_TOKEN. Sign in once: git credential-manager github login"
    Write-Host "Or set `$env:GITHUB_TOKEN and re-run: .\push_main.ps1 $Branch"
    exit 1
}

$repoUrl = "https://x-access-token:$token@github.com/OrderofChaos33/sincor2.git"
git push $repoUrl "${Branch}:${Branch}" 2>&1 | ForEach-Object { $_ -replace [regex]::Escape($token), "***" }
$token = $null
$repoUrl = $null
Write-Host "Pushed $Branch (merge to main on GitHub when ready)"