# Assemble-SINCOR.ps1
# Automatically rebuilds the SINCOR folder into the MVP layout

# 1. Create required folders
@('automation','constitution','tokenomics') | ForEach-Object {
  if (-not (Test-Path $_)) { New-Item -ItemType Directory -Name $_ | Out-Null }
}

# 2. Move automation bits
if (Test-Path '.\config') {
  Move-Item '.\config\*'    '.\automation\' -Force -ErrorAction SilentlyContinue
}
if (Test-Path '.\scripts\hvmwop-sync-agent.py') {
  Move-Item '.\scripts\hvmwop-sync-agent.py' '.\automation\' -Force -ErrorAction SilentlyContinue
}

# 3. Pull docs into proper places
if (Test-Path '.\docs') {
  Move-Item '.\docs\HVMWOP-Constitution.md'  '.\constitution\'   -Force -ErrorAction SilentlyContinue
  Move-Item '.\docs\quadrants.md'           '.\'               -Force -ErrorAction SilentlyContinue
  Move-Item '.\docs\Flow-Logic.md'          '.\tokenomics\'    -Force -ErrorAction SilentlyContinue
}

# 4. Move web UI files to root
if (Test-Path '.\public') {
  Move-Item '.\public\index.html' '.\' -Force -ErrorAction SilentlyContinue
  Move-Item '.\public\*.css'      '.\' -Force -ErrorAction SilentlyContinue
  Move-Item '.\public\*.js'       '.\' -Force -ErrorAction SilentlyContinue
}

# 5. Cleanup unneeded folders
@('logs','test_payloads','agentmode') | ForEach-Object {
  if (Test-Path $_) { Remove-Item $_ -Recurse -Force }
}

# 6. Verify agent files exist
@('Growth-Agent.md','Meta-Agent.md','Ops-Agent.md','Treasury-Agent.md') | ForEach-Object {
  if (-not (Test-Path ".\agents\$_")) { Write-Warning "Missing agents\$_" }
}

# 7. Display final structure
Write-Host "`nFinal SINCOR layout:`n"
Get-ChildItem -Recurse | Format-List

Write-Host "`n Assembly complete.`nRun:`n  cd `"$PWD`"`n  python3 -m http.server 8000 --directory `"$PWD`"`n  python3 .\automation\hvmwop-sync-agent.py`n"
