# --- load path manifest ---
$manifestPath = Join-Path $PATHS_DIR ($path + ".json")
if (-not (Test-Path $manifestPath)) {
  Write-Error "Path manifest not found: $manifestPath"
  exit 2
}
$pathObj = Get-Content -Raw $manifestPath | ConvertFrom-Json

# --- start only required services (python entrypoints in /services) ---
$procs = @()
foreach ($svc in $pathObj.services) {
  $entry = Join-Path $SERVICES_DIR ($svc + ".py")
  if (-not (Test-Path $entry)) {
    Write-Warning "Missing service entry: $entry (skipping)"
    continue
  }
  $out = Join-Path $LOGDIR ($svc + ".out")
  $err = Join-Path $LOGDIR ($svc + ".err")
  $p = Start-Process -FilePath "python" -ArgumentList $entry `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
  $procs += $p
  Start-Sleep -Milliseconds 200
}

# --- drop a job file into runtime/inbox for services to consume ---
$job = @{
  id      = "$($path)-$TIMESTAMP"
  path    = $path
  biz     = $biz
  city    = $city
  email   = $email
  phone   = $phone
  price   = $price
  payURL  = $payURL
  ts      = $TIMESTAMP
} | ConvertTo-Json -Depth 6

$jobfile = Join-Path $INBOX ("job_" + $TIMESTAMP + ".json")
$job | Set-Content -Path $jobfile -Encoding UTF8

Write-Host "Job queued: $jobfile"
Write-Host "Waiting up to 5 minutes for runtime\write_manifest.json ..."

# --- wait for services to produce the write manifest ---
$deadline = (Get-Date).AddMinutes(5)
while ((Get-Date) -lt $deadline) {
  if (Test-Path $WF) { break }
  Start-Sleep -Milliseconds 500
}

if (-not (Test-Path $WF)) {
  Write-Host "Timed out. Check logs in $LOGDIR"
  exit 1
}

# --- preview planned writes via write_gate.py (no disk writes yet) ---
$WG = Join-Path $BASE "write_gate.py"
if (-not (Test-Path $WG)) {
  Write-Error "write_gate.py not found at $WG"
  exit 3
}

Write-Host "`n=== WRITE PREVIEW ==="
python $WG preview $WF | Tee-Object -FilePath (Join-Path $LOGDIR "write_preview.json") | Out-Null
Write-Host "Preview saved: $LOGDIR\write_preview.json"
Write-Host "To APPLY writes, run:"
Write-Host "python `"$WG`" apply `"$WF`" | Tee-Object -FilePath `"$($LOGDIR)\write_apply.json`""
