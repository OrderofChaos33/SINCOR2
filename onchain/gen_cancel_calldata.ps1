$cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
$HOOK = "0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0"
$USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
$SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
$RECIP = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
$key = "($USDC,$SINC,3000,60,$HOOK)"
$rungs = @(
    @{ tick = 41880; label = '$1.50'; sinc = '5M' },
    @{ tick = 34920; label = '$3.00'; sinc = '4M' },
    @{ tick = 25620; label = '$7.50'; sinc = '4M' },
    @{ tick = 17880; label = '$15'; sinc = '3M' },
    @{ tick = 6900; label = '$40'; sinc = '2M' },
    @{ tick = 0; label = '$100'; sinc = '2M' }
)
$out = @()
foreach ($r in $rungs) {
    $data = (& $cast calldata "cancelOrder((address,address,uint24,int24,address),int24,bool,address)" $key $r.tick false $RECIP).Trim()
    $out += [ordered]@{ tick = $r.tick; label = $r.label; sinc = $r.sinc; data = $data }
}
$out | ConvertTo-Json -Depth 4