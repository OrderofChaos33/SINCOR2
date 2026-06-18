Set-Location C:\Users\cjay4\OneDrive\Desktop\sincor-clean\onchain
$pk = (Get-Content .env | Where-Object { $_ -match "^DEPLOYER_PRIVATE_KEY=" }) -replace "^DEPLOYER_PRIVATE_KEY=", ""
& "$env:USERPROFILE\.foundry\bin\cast.exe" send 0x75dE341a2BC81806198364F125d4Cde36527619C "buy(uint256,address)" 500000000000000 0x0000000000000000000000000000000000000000 --value 500000000000000 --rpc-url base --private-key $pk
$pk = $null
