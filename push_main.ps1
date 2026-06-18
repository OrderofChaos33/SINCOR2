Set-Location C:\Users\cjay4\OneDrive\Desktop\sincor-clean
$envFile = "C:\Users\cjay4\.grok\worktrees\desktop-sincor-clean\sincor\onchain\.env"
$pat = $null
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^GITHUB_PAT=(.+)$') { $pat = $matches[1].Trim(); break }
        if ($line -match '^github_pat_') { $pat = $line.Trim(); break }
    }
}
if (-not $pat -and (Test-Path "C:\Users\cjay4\OneDrive\Desktop\github_pat.txt")) {
    $pat = (Get-Content "C:\Users\cjay4\OneDrive\Desktop\github_pat.txt" -Raw).Trim()
}
if (-not $pat) { throw "No GitHub PAT in onchain/.env or Desktop/github_pat.txt" }
$repoUrl = "https://x-access-token:$pat@github.com/OrderofChaos33/sincor2.git"
$output = git push $repoUrl main 2>&1
$sanitized = ($output | ForEach-Object { $_ -replace [regex]::Escape($pat), "***REDACTED***" })
$sanitized
$pat = $null
$repoUrl = $null
