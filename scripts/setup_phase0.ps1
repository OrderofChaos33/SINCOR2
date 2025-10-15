# Phase 0 - Setup & Safety
# SINCOR Consolidation Mission

$env:TZ = "America/Chicago"
$STAGING = "C:\_sincor_consolidation"
$REPORTS = "$STAGING\reports"
$LOGS = "$STAGING\logs"

# Create directories
New-Item -ItemType Directory -Force -Path $STAGING | Out-Null
New-Item -ItemType Directory -Force -Path $REPORTS | Out-Null
New-Item -ItemType Directory -Force -Path $LOGS | Out-Null

# Define paths
$S2 = "C:\Users\cjay4\OneDrive\Desktop\SINCOR2"
$CLEAN = "C:\Users\cjay4\OneDrive\Desktop\sincor-clean"

# Define root search paths
$ROOTS = @(
  "$HOME\Desktop",
  "$HOME\Documents",
  "$HOME\Downloads",
  "$HOME\OneDrive\Desktop",
  "$HOME\OneDrive\Documents",
  "C:\",
  "D:\",
  "E:\"
)

# Save roots to file
$ROOTS | Out-File "$REPORTS\roots.txt" -Encoding utf8

# Verify critical paths exist
Write-Host "=== Phase 0: Setup & Safety Checks ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Staging Directory: $STAGING" -ForegroundColor Green
Write-Host "Reports Directory: $REPORTS" -ForegroundColor Green
Write-Host "Logs Directory: $LOGS" -ForegroundColor Green
Write-Host ""

if (Test-Path $S2) {
    Write-Host "[OK] SINCOR2 found at: $S2" -ForegroundColor Green
} else {
    Write-Host "[ERROR] SINCOR2 not found at: $S2" -ForegroundColor Red
    exit 1
}

if (Test-Path $CLEAN) {
    Write-Host "[OK] sincor-clean found at: $CLEAN" -ForegroundColor Green
} else {
    Write-Host "[ERROR] sincor-clean not found at: $CLEAN" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Search roots:" -ForegroundColor Yellow
foreach ($root in $ROOTS) {
    if (Test-Path $root) {
        Write-Host "  [OK] $root" -ForegroundColor Green
    } else {
        Write-Host "  [SKIP] $root (not found)" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "=== Phase 0 Complete ===" -ForegroundColor Cyan
Write-Host "Ready to proceed to Phase 1 (Device-Wide Discovery)" -ForegroundColor Yellow
