# Repair git repo after OneDrive/Google Drive desktop.ini corruption.
$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent

# Find main .git (worktree may point to Desktop)
$gitFile = Join-Path $Root ".git"
$gitDir = $Root
if (Test-Path $gitFile -PathType Leaf) {
    $line = Get-Content $gitFile -Raw
    if ($line -match "gitdir:\s*(.+)") {
        $gitDir = $Matches[1].Trim() -replace "/","\"
        $gitDir = Split-Path $gitDir -Parent
    }
} else {
    $gitDir = $gitFile
}

Write-Host "Git dir: $gitDir"
$inis = Get-ChildItem -Path $gitDir -Recurse -Filter "desktop.ini" -Force -ErrorAction SilentlyContinue
if ($inis) {
    $inis | Remove-Item -Force
    Write-Host "Removed $($inis.Count) desktop.ini from .git"
}

Set-Location $Root
python scripts/git_repair_objects.py
git add -A
Write-Host "Repair pass done. Try: git checkout -B launch/kpi-campaign-2026 && git commit"