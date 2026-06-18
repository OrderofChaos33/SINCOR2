# Fork + push + open Superchain token-list PR for SINC on Base.
# Requires one-time: gh auth login
# Usage: .\scripts\submit_superchain_pr.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$gh = Join-Path $root "tools\gh\bin\gh.exe"
if (-not (Test-Path $gh)) { $gh = "gh" }

function Get-GithubPat {
    $envFile = Join-Path $root "onchain\.env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match '^GITHUB_PAT=(.+)$') { return $matches[1].Trim() }
            if ($line -match '^github_pat_') { return $line.Trim() }
        }
    }
    $legacy = Join-Path $env:USERPROFILE "OneDrive\Desktop\github_pat.txt"
    if (Test-Path $legacy) { return (Get-Content $legacy -Raw).Trim() }
    return $null
}

$pat = Get-GithubPat
if ($pat) { $env:GH_TOKEN = $pat }

$repoDir = Join-Path $root "tokenlists\_staging\ethereum-optimism.github.io"
if (-not (Test-Path $repoDir)) {
    python (Join-Path $root "scripts\whitelist_token.py") launch | Out-Null
}

Write-Host "Checking GitHub auth ..."
& $gh auth status 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "GitHub login required (one time). A browser window will open."
    & $gh auth login --web --git-protocol https --hostname github.com
    if ($LASTEXITCODE -ne 0) { throw "gh auth login failed" }
}

Push-Location $repoDir
try {
    $user = (& $gh api user -q .login).Trim()
    Write-Host "Authenticated as $user"

    $fork = "https://github.com/$user/ethereum-optimism.github.io.git"
    $exists = $true
    try { & $gh repo view "$user/ethereum-optimism.github.io" --json name -q .name 2>$null | Out-Null } catch { $exists = $false }
    if (-not $exists) {
        Write-Host "Creating fork under $user ..."
        & $gh repo fork ethereum-optimism/ethereum-optimism.github.io --clone=false
        if ($LASTEXITCODE -ne 0) { throw "fork failed" }
    }

    & git remote set-url origin $fork
    $branch = (& git branch --show-current).Trim()
    if (-not $branch) { $branch = "add-sinc-20260612" }

    Write-Host "Pushing $branch to $fork ..."
    & git push -u origin $branch
    if ($LASTEXITCODE -ne 0) { throw "git push failed" }

    Write-Host "Opening pull request ..."
    & $gh pr create `
        --repo ethereum-optimism/ethereum-optimism.github.io `
        --head "$user`:$branch" `
        --base master `
        --title "Add SINC token on Base" `
        --body "Adds SINC (0x9C8cd8d3961F445D653713dE65C6578bE11668e7) on Base. Native Base ERC-20, nobridge. Website https://getsincor.com. Sourcify-verified, CertiK Skynet 97/100."

    if ($LASTEXITCODE -eq 0) {
        Write-Host "PR created successfully."
    } else {
        Write-Host "PR may already exist — open compare page:"
        Write-Host "https://github.com/ethereum-optimism/ethereum-optimism.github.io/compare/master...${user}:${branch}?expand=1"
    }
}
finally {
    Pop-Location
}