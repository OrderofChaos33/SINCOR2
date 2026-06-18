# Read-only sweep: Aerodrome, CoW/BCoW, Balancer, Uniswap v4, common Base tokens
param(
    [string]$Creator = "0x35cb3bf1b29F81d325EB9A7225a3E87fE7B37D6f",
    [string]$Treasury = "0xAf9B539D8043C634b7E611818518BA7E850F289e"
)

$ErrorActionPreference = "Continue"
$cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$envFile = Join-Path $here ".env"
if (-not (Test-Path $envFile)) {
    $fallback = "C:\Users\cjay4\OneDrive\Desktop\sincor-clean\onchain\.env"
    if (Test-Path $fallback) { Copy-Item $fallback $envFile -Force }
}
$lines = Get-Content $envFile
$rpc = ($lines | Where-Object { $_ -match "^BASE_RPC_URL=(.+)$" } | ForEach-Object { $matches[1] } | Select-Object -First 1)
if (-not $rpc) { $rpc = "https://mainnet.base.org" }

function Invoke-CastCall([string]$to, [string]$sig, [string[]]$args = @()) {
    $cmd = @("call", $to, $sig) + $args + @("--rpc-url", $rpc)
    $out = & $cast @cmd 2>&1
    if ($LASTEXITCODE -ne 0) { return $null }
    return ($out | Out-String).Trim()
}

function Show-Balance([string]$label, [string]$wallet, [string]$token, [string]$sig = "balanceOf(address)(uint256)") {
    $v = Invoke-CastCall $token $sig $wallet
    if ($v -and $v -notmatch "^0$" -and $v -notmatch "^0 \[") {
        Write-Host "  $label : $v"
        return $true
    }
    return $false
}

$tokens = @{
    SINC       = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
    LEGACY     = "0x49E392de962Fa835B862F59E78611c69E930b5C4"
    AXM        = "0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822"
    USDC       = "0x833589fCD6eDb6E08f4c7B32D6Ff2Fd83893a7"
    USDCb      = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    WETH       = "0x4200000000000000000000000000000000000006"
    AERO       = "0x940181a94A35A7119A45D2442Fa466d8143D6AF6"
    BCoW_BPT   = "0x6FDdC59f3a84E95685c6874D1Da45B3663b88E95"
    BAL_LBP    = "0x3b35E92c4f34B8468659612B9742248185F04B00"
}
$infra = @{
    POSM       = "0x7C5f5A4bBd8fD63184577525326123B519429bDc"
    VE         = "0xeBf418Fe2512e7E6bd9b87a8F0f294aCDC67e6B4"
    AERO_FACT  = "0x420DD381b31aEf6683db6b902084cB0FFECe40Da"
    LOCKER     = "0x9047c0944c843d91951a6C91dc9f3944D826ACA8"
    GENESIS    = "0xF3Bd56788b5E56DE638AF5dDffFA478838A68d09"
}

Write-Host "=== Wallet sweep (Base) ==="
Write-Host "RPC: $rpc"
$ts = Invoke-CastCall "latest" "timestamp()(uint256)" @()
Write-Host "block.timestamp: $ts"
Write-Host ""

foreach ($w in @(@{n="CREATOR";a=$Creator}, @{n="TREASURY";a=$Treasury})) {
    Write-Host "--- $($w.n) $($w.a) ---"
    $eth = & $cast balance $w.a --rpc-url $rpc 2>&1
    Write-Host "  ETH wei: $eth"
    foreach ($k in $tokens.Keys) {
        Show-Balance $k $w.a $tokens[$k] | Out-Null
    }
    $v4 = Invoke-CastCall $infra.POSM "balanceOf(address)(uint256)" $w.a
    $ve = Invoke-CastCall $infra.VE "balanceOf(address)(uint256)" $w.a
    $gn = Invoke-CastCall $infra.GENESIS "balanceOf(address)(uint256)" $w.a
    Write-Host "  v4_PositionNFTs: $v4"
    Write-Host "  veAERO_NFTs: $ve"
    Write-Host "  GenesisNFTs: $gn"
    Write-Host ""
}

Write-Host "--- BCoW pool ---"
$ft = Invoke-CastCall $tokens.BCoW_BPT "getFinalTokens()(address[])"
Write-Host "  final tokens: $ft"

Write-Host "--- Aerodrome volatile pools (LP balance) ---"
$pairTokens = @($tokens.SINC, $tokens.AXM, $tokens.LEGACY)
$quotes = @($tokens.WETH, $tokens.USDC)
foreach ($t in $pairTokens) {
    foreach ($q in $quotes) {
        $pool = Invoke-CastCall $infra.AERO_FACT "getPool(address,address,bool)(address)" @($t, $q, "false")
        if ($pool -and $pool -notmatch "0x0000000000000000000000000000000000000000") {
            foreach ($w in @($Creator, $Treasury)) {
                $lp = Invoke-CastCall $pool "balanceOf(address)(uint256)" $w
                if ($lp -and $lp -notmatch "^0$") {
                    Write-Host "  pool $pool ($t/$q) $w LP=$lp"
                }
            }
        }
    }
}

Write-Host "--- PumpClaw (creator fee recipient) ---"
$pos = Invoke-CastCall $infra.LOCKER "getPosition(address)(uint256,address)" $tokens.AXM
Write-Host "  AXM position: $pos"

Write-Host "--- Fjord fixed-price (treasury-owned) ---"
$fjord = @(
    "0xff38C22C5932Cf4283F33A892763FCCDe2EEa6aD",
    "0xa497DB488e5d6aCCE3CaB5fBe19cB5C63de91959",
    "0x2feDA1347981dCBdcbd249E28B0f5897A5043CCC"
)
foreach ($p in $fjord) {
    $owner = Invoke-CastCall $p "owner()(address)"
    $end = Invoke-CastCall $p "saleEnd()(uint256)"
    $close = Invoke-CastCall $p "canClose()(bool)"
    $sincHeld = Invoke-CastCall $tokens.SINC "balanceOf(address)(uint256)" $p
    Write-Host "  $p owner=$owner saleEnd=$end canClose=$close SINC=$sincHeld"
}

Write-Host ""
Write-Host "--- Basescan token list (ERC-20 holdings) ---"
foreach ($w in @(@{n="creator";a=$Creator}, @{n="treasury";a=$Treasury})) {
    try {
        $url = "https://api.basescan.org/api?module=account&action=tokenlist&address=$($w.a)"
        $r = Invoke-RestMethod -Uri $url -TimeoutSec 30
        if ($r.status -eq "1" -and $r.result) {
            Write-Host "$($w.n) tokens with balance:"
            foreach ($t in $r.result) {
                if ([decimal]$t.balance -gt 0) {
                    $sym = if ($t.symbol) { $t.symbol } else { "?" }
                    Write-Host "  $sym ($($t.contractAddress)) bal=$($t.balance) dec=$($t.decimals)"
                }
            }
        } else {
            Write-Host "$($w.n): basescan $($r.message)"
        }
    } catch {
        Write-Host "$($w.n): basescan error $_"
    }
}