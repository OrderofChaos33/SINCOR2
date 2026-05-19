$cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
$envPath = "C:\Users\cjay4\OneDrive\Desktop\sincor-clean\onchain\.env"

$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$hex = -join ($bytes | ForEach-Object { $_.ToString("x2") })
$privKey = "0x" + $hex

$addr = (& $cast wallet address --private-key $privKey).Trim()

if (-not $addr.StartsWith("0x")) {
    Write-Host "ERROR: cast wallet address returned: '$addr'"
    exit 1
}

$c = Get-Content $envPath
$c = $c -replace "^DEPLOYER_PRIVATE_KEY=.*", "DEPLOYER_PRIVATE_KEY=$privKey"
$c = $c -replace "^TREASURY=.*", "TREASURY=$addr"
Set-Content -Path $envPath -Value $c

Write-Host ""
Write-Host "================================"
Write-Host " WALLET GENERATED"
Write-Host "================================"
Write-Host " ADDRESS: $addr"
Write-Host ""
Write-Host " Private key written to .env (gitignored)"
Write-Host " Reply to Claude with the ADDRESS"
Write-Host "================================"
