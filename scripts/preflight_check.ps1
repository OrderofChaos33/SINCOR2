# Pre-launch verification — run this BEFORE pressing T-0 publish
# Returns OK / WARN / FAIL on each gate

$ErrorActionPreference = "Continue"

$root = "C:\Users\cjay4\OneDrive\Desktop\sincor-clean"
$cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
$forge = "$env:USERPROFILE\.foundry\bin\forge.exe"

$pass = 0
$warn = 0
$fail = 0

function Check {
    param([string]$name, [string]$status, [string]$detail)
    $color = switch ($status) {
        "OK"   { "Green" }
        "WARN" { "Yellow" }
        "FAIL" { "Red" }
    }
    Write-Host "[$status]" -ForegroundColor $color -NoNewline
    Write-Host " $name"
    if ($detail) { Write-Host "      $detail" -ForegroundColor DarkGray }
    switch ($status) {
        "OK"   { $script:pass++ }
        "WARN" { $script:warn++ }
        "FAIL" { $script:fail++ }
    }
}

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host " SINC LAUNCH PRE-FLIGHT CHECK" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# 1. .env vars present
$env = Get-Content "$root\onchain\.env" -ErrorAction SilentlyContinue
if (-not $env) {
    Check ".env exists" "FAIL" "Missing $root\onchain\.env"
} else {
    $hasKey = ($env | Where-Object { $_ -match "^DEPLOYER_PRIVATE_KEY=0x[a-fA-F0-9]{64}$" }).Count -gt 0
    $hasSinc = ($env | Where-Object { $_ -match "^SINC_TOKEN=0x9C8cd8d3961F445D653713dE65C6578bE11668e7$" }).Count -gt 0
    $hasMainnetPM = ($env | Where-Object { $_ -match "^POOL_MANAGER=0x498581fF718922c3f8e6A244956aF099B2652b2b$" }).Count -gt 0
    $hasSalt = ($env | Where-Object { $_ -match "^HOOK_SALT=0x[a-fA-F0-9]{64}$" }).Count -gt 0
    if ($hasKey) { Check ".env DEPLOYER_PRIVATE_KEY present" "OK" "" } else { Check ".env DEPLOYER_PRIVATE_KEY" "FAIL" "Missing or malformed" }
    if ($hasSinc) { Check ".env SINC_TOKEN = canonical" "OK" "" } else { Check ".env SINC_TOKEN" "FAIL" "Not set to 0x9C8c…68e7" }
    if ($hasMainnetPM) { Check ".env POOL_MANAGER = Base mainnet V4" "OK" "" } else { Check ".env POOL_MANAGER" "FAIL" "Not set to mainnet V4 0x498581ff…" }
    if ($hasSalt) { Check ".env HOOK_SALT mined" "OK" "" } else { Check ".env HOOK_SALT" "FAIL" "Not mined" }
}

# 2. Deployer funded?
$bal = & $cast balance 0x7B4082f78CdAc2cB5fa8572b2CA54BeDaaa8f956 --rpc-url "$root\onchain\base" 2>$null
if (-not $bal -or $LASTEXITCODE -ne 0) {
    # Try with full RPC URL
    $rpcLine = ($env | Where-Object { $_ -match "^BASE_RPC_URL=" }) -replace "^BASE_RPC_URL=", ""
    if ($rpcLine) {
        $bal = & $cast balance 0x7B4082f78CdAc2cB5fa8572b2CA54BeDaaa8f956 --rpc-url $rpcLine 2>$null
    }
}
if ($bal) {
    $balInt = [decimal]$bal
    $ethBal = $balInt / 1000000000000000000
    if ($balInt -ge 2000000000000000) {
        Check "Deployer 0x7B40 funded on Base mainnet" "OK" "$ethBal ETH"
    } else {
        Check "Deployer 0x7B40 funded on Base mainnet" "FAIL" "Has $ethBal ETH, need ≥ 0.002 ETH"
    }
} else {
    Check "Deployer 0x7B40 balance check" "WARN" "Could not query balance — check RPC"
}

# 3. Unit tests still green?
Push-Location "$root\onchain"
$testOut = & $forge test --no-match-contract IntegrationTest 2>&1 | Select-String -Pattern "tests passed,"
Pop-Location
if ($testOut -match "(\d+) tests passed, 0 failed") {
    $passed = $Matches[1]
    Check "Unit tests passing" "OK" "$passed tests passed"
} else {
    Check "Unit tests passing" "FAIL" "Run forge test manually to diagnose"
}

# 4. Contracts deployed?
$deployPath = "$root\onchain\deployments\8453.json"
if (Test-Path $deployPath) {
    $deploy = Get-Content $deployPath -Raw | ConvertFrom-Json
    if ($deploy.nft -and $deploy.nft -ne "0x0" -and
        $deploy.curve -and $deploy.curve -ne "0x0" -and
        $deploy.hook -and $deploy.hook -ne "0x0") {
        Check "Mainnet contracts deployed" "OK" "nft=$($deploy.nft) curve=$($deploy.curve) hook=$($deploy.hook)"
    } else {
        Check "Mainnet contracts deployed" "FAIL" "deployments/8453.json has zero/missing addresses"
    }
} else {
    Check "Mainnet contracts deployed" "WARN" "deployments/8453.json not found — deploys not run yet"
}

# 5. Templates: any v2/holder/liquidity leaks?
$forbidden = @("v2 died", "37 holders", "1,100 holders", "dead LP", "broken liquidity")
$leakFiles = @()
foreach ($f in @("$root\templates\sinc_gateway.html", "$root\templates\axiom.html", "$root\templates\home.html", "$root\outputs\launch_content_kit_DRAFT.md")) {
    if (Test-Path $f) {
        $content = Get-Content $f -Raw
        foreach ($phrase in $forbidden) {
            if ($content -match [regex]::Escape($phrase)) {
                $leakFiles += "$f leaks: $phrase"
            }
        }
    }
}
if ($leakFiles.Count -eq 0) {
    Check "No forbidden phrases (v2/37/holders/liquidity) in templates+kit" "OK" ""
} else {
    Check "Forbidden phrases leaked" "FAIL" ($leakFiles -join "; ")
}

# 6. Broken /buy-sinc links?
$buySincRefs = Get-ChildItem "$root\templates" -Filter "*.html" -Recurse | Select-String -Pattern "/buy-sinc" -SimpleMatch
if ($buySincRefs.Count -eq 0) {
    Check "No /buy-sinc broken links in templates" "OK" ""
} else {
    Check "/buy-sinc broken links" "FAIL" ($buySincRefs | ForEach-Object { "$($_.Filename):$($_.LineNumber)" } -join ", ")
}

# 7. Content kit + runbooks present?
$artifacts = @(
    "$root\outputs\launch_content_kit_DRAFT.md",
    "$root\outputs\certik_skynet_submission.md",
    "$root\outputs\airdrop_round1_disperse.txt",
    "$root\docs\superpowers\runbooks\2026-05-19-launch-day-operations.md",
    "$root\scripts\post_deploy_template_update.ps1"
)
foreach ($a in $artifacts) {
    if (Test-Path $a) {
        Check "Artifact: $(Split-Path $a -Leaf)" "OK" ""
    } else {
        Check "Artifact: $(Split-Path $a -Leaf)" "FAIL" "Missing"
    }
}

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host " PASS: $pass    WARN: $warn    FAIL: $fail" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan

if ($fail -gt 0) {
    Write-Host ""
    Write-Host "Cannot proceed to T-0 publish with FAILs. Fix and re-run." -ForegroundColor Red
    exit 1
} elseif ($warn -gt 0) {
    Write-Host ""
    Write-Host "Proceeding is your call — WARNs are not blockers but worth investigating." -ForegroundColor Yellow
    exit 0
} else {
    Write-Host ""
    Write-Host "ALL CLEAR. Cleared for T-0." -ForegroundColor Green
    exit 0
}
