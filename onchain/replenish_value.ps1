# Replace lost SINC inventory — audit, plan, curve buy, bot watch.
# Usage:
#   .\replenish_value.ps1 audit
#   .\replenish_value.ps1 plan
#   .\replenish_value.ps1 buy-curve -Eth 0.33
#   .\replenish_value.ps1 buy-curve -SincAtoms 982854705556465
#   .\replenish_value.ps1 sweep-old-safe

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("audit", "plan", "buy-curve", "sweep-old-safe")]
    [string]$Action,

    [string]$Eth = "",
    [string]$SincAtoms = "982854705556465",
    [string]$PrivateKey = ""
)

$ErrorActionPreference = "Stop"
$cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
if (-not (Test-Path $cast)) { throw "cast not found" }

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$envFile = Join-Path $here ".env"
$rpc = "https://mainnet.base.org"
if (Test-Path $envFile) {
    $line = Get-Content $envFile | Where-Object { $_ -match "^BASE_RPC_URL=(.+)$" } | Select-Object -First 1
    if ($line -match "^BASE_RPC_URL=(.+)$") { $rpc = $matches[1].Trim() }
}

$SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
$CURVE = "0x75dE341a2BC81806198364F125d4Cde36527619C"
$PM = "0x498581fF718922c3f8e6A244956aF099B2652b2b"
$BOT = "0x66DE38dA216D6fCC3F9Aa944f592546e3eae2dD0"
$NEW = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
$OLD_SAFE = "0x2d61752adF5092052Ff7D366a9884823C07Cdaf8"
$LOST = "982854705556465"
$PAIR = "0x85372932f9b151a076815d92cf71a97980ffd667"

function Get-Atoms([string]$raw) { return ($raw -split '\s+')[0] }
function Get-EthDecimal([string]$wei) { return [decimal]$wei / 1000000000000000000 }

function Get-BalanceAtoms([string]$addr) {
    $raw = (& $cast call $SINC "balanceOf(address)(uint256)" $addr --rpc-url $rpc).Trim()
    return Get-Atoms $raw
}

function Load-SignerKey() {
    if ($PrivateKey) {
        $pk = $PrivateKey.Trim()
        if (-not $pk.StartsWith("0x")) { $pk = "0x$pk" }
        return $pk
    }
    $emergency = Join-Path $here ".emergency_wallet.json"
    if (Test-Path $emergency) {
        $j = Get-Content $emergency -Raw | ConvertFrom-Json
        if ($j.private_key) { return $j.private_key }
    }
    if (Test-Path $envFile) {
        $pk = (Get-Content $envFile | Where-Object { $_ -match "^TREASURY_PRIVATE_KEY=(.+)$" -and $_ -notmatch "= *$" } | ForEach-Object { $matches[1] } | Select-Object -First 1)
        if ($pk) { return $pk.Trim() }
    }
    throw "No key. Import new treasury to MetaMask or pass -PrivateKey (never paste in chat)."
}

function Show-Audit {
    Write-Host "=== SINC inventory audit (Base) ===" -ForegroundColor Cyan
    $rows = @(
        @{n="NEW treasury"; a=$NEW},
        @{n="OLD safe (abandon)"; a=$OLD_SAFE},
        @{n="Bonding curve"; a=$CURVE},
        @{n="PoolManager (hook sells)"; a=$PM},
        @{n="MEV bot (stolen)"; a=$BOT},
        @{n="Rogue V2 pair"; a=$PAIR}
    )
    foreach ($r in $rows) {
        $atoms = Get-BalanceAtoms $r.a
        $tokens = [math]::Round([decimal]$atoms / 100000000, 2)
        Write-Host ("{0,-28} {1,18} atoms  (~{2:N0} SINC)" -f $r.n, $atoms, $tokens)
    }
    $ethNew = (& $cast balance $NEW --rpc-url $rpc -e).Trim()
    Write-Host ""
    Write-Host "NEW treasury ETH: $ethNew"
}

function Show-Plan {
    Show-Audit
    Write-Host ""
    Write-Host "=== Replenishment plan for ~9.83M lost SINC ===" -ForegroundColor Cyan

    $costWei = Get-Atoms (& $cast call $CURVE "getBuyCost(uint256)(uint256)" $LOST --rpc-url $rpc).Trim()
    $costEth = Get-EthDecimal $costWei
    $priceWei = Get-Atoms (& $cast call $CURVE "currentPriceWei()(uint256)" --rpc-url $rpc).Trim()
    $pmAtoms = Get-BalanceAtoms $PM

    Write-Host ""
    Write-Host "PRIORITY 1 - Sweep curve inventory to treasury (closes sub-floor public buys)" -ForegroundColor Green
    Write-Host "  Cost to replace full 9.83M: ~$([math]::Round($costEth, 4)) ETH (~`$$([math]::Round($costEth * 3000, 0)) at `$3000/ETH)"
    $spotEthPerSinc = [decimal]$priceWei / 1000000000000000000
    Write-Host "  Curve micro-spot ~`$$([math]::Round($spotEthPerSinc * 3000, 6)) — NOT public price; official floor is `$1.50/SINC"
    Write-Host "  Command: .\close_curve_cheap_buys.ps1 -DryRun  then  .\close_curve_cheap_buys.ps1"
    Write-Host "  Needs: ~$([math]::Round($costEth, 4)) ETH on NEW treasury $NEW"
    Write-Host ""
    Write-Host "PRIORITY 2 - Watch bot for cheap buyback" -ForegroundColor Yellow
    Write-Host "  Bot still holds ~9.83M. If it dumps to rogue pool, buy for pennies."
    Write-Host "  Command: .\watch_bot_wallet.ps1"
    Write-Host ""
    Write-Host "PRIORITY 3 - Hook inventory (not lost)" -ForegroundColor Yellow
    $pmM = [math]::Round([decimal]$pmAtoms / 100000000 / 1000000, 2)
    Write-Host "  PoolManager holds ~${pmM}M SINC in live sell orders."
    Write-Host "  That is working inventory, not stolen. Do not cancel unless re-placing ladder."
    Write-Host ""
    Write-Host "PRIORITY 4 - Sweep OLD safe dust" -ForegroundColor DarkGray
    Write-Host "  ~171K SINC may still sit on compromised 0x2d61. Run sweep-old-safe once."
    Write-Host ""
    Write-Host "NOT `$10M cash - `$10M assumes `$1/SINC hook floor. Curve spot replace is ~`$1k ETH." -ForegroundColor Magenta
}

function Invoke-BuyCurve {
    $pk = Load-SignerKey
    $signer = (& $cast wallet address --private-key $pk).Trim()
    if ($signer -ine $NEW) {
        Write-Host "Signer is $signer (expected NEW treasury $NEW)" -ForegroundColor Yellow
    }

    [string]$ethWei = ""
    if ($Eth) {
        $ethF = [double]$Eth
        $ethWei = [string][bigint]([math]::Floor($ethF * 1e18))
    } else {
        $ethWei = Get-Atoms (& $cast call $CURVE "getBuyCost(uint256)(uint256)" $SincAtoms --rpc-url $rpc).Trim()
    }

    $preview = Get-Atoms (& $cast call $CURVE "getBuyAmount(uint256)(uint256)" $ethWei --rpc-url $rpc).Trim()
    $balWei = (& $cast balance $signer --rpc-url $rpc).Trim()
    Write-Host "Buying with $ethWei wei ETH -> est $preview SINC atoms to $signer"
    Write-Host "Wallet ETH: $balWei wei"

    if ([bigint]$balWei -lt [bigint]$ethWei) {
        throw "Need ~$(Get-EthDecimal $ethWei) ETH on $signer. Bridge/fund Base ETH first, then re-run."
    }

    & $cast send $CURVE "buy(uint256,address)" $ethWei "0x0000000000000000000000000000000000000000" --value $ethWei --rpc-url $rpc --private-key $pk
    if ($LASTEXITCODE -ne 0) { throw "Curve buy failed" }

    $after = Get-BalanceAtoms $signer
    Write-Host "Done. Treasury SINC atoms: $after" -ForegroundColor Green
}

function Invoke-SweepOldSafe {
    $pk = Load-SignerKey
    $signer = (& $cast wallet address --private-key $pk).Trim()
    if ($signer -ine $OLD_SAFE) {
        throw "Key is $signer not OLD safe $OLD_SAFE. Use compromised safe key once, then abandon."
    }
    $bal = Get-BalanceAtoms $OLD_SAFE
    if ([decimal]$bal -le 0) { Write-Host "OLD safe SINC already 0"; return }
    Write-Host "Sweeping $bal SINC -> $NEW"
    & $cast send $SINC "transfer(address,uint256)" $NEW $bal --rpc-url $rpc --private-key $pk
    Write-Host "Sweep tx sent." -ForegroundColor Green
}

switch ($Action) {
    "audit" { Show-Audit; break }
    "plan" { Show-Plan; break }
    "buy-curve" { Invoke-BuyCurve; break }
    "sweep-old-safe" { Invoke-SweepOldSafe; break }
}