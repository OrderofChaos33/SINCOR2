# SINC recovery orchestrator (Base mainnet)
# Usage:
#   .\recover_liquidity.ps1 status            # read-only chain checks
#   .\recover_liquidity.ps1 sweep             # full wallet/protocol scan
#   .\recover_liquidity.ps1 calldata          # print sign-ready txs (all 3 paths)
#   .\recover_liquidity.ps1 aerodrome         # treasury Aerodrome Slipstream collect fees
#   .\recover_liquidity.ps1 aero-exit         # treasury full Aero LP exit (decrease+collect+burn)
#   .\recover_liquidity.ps1 fjord-wait        # poll until saleEnd, then close x3
#   .\recover_liquidity.ps1 fjord-wait -PlaceFloorAfter  # close Fjord then place $1.50+ ladder
#   .\recover_liquidity.ps1 fjord             # close Fjord now (fails if sale active)
#   .\recover_liquidity.ps1 bcow              # BCoW exit (CSW key) or print calldata
#   .\recover_liquidity.ps1 creator           # creator LP/Balancer unwind guide
#   .\recover_liquidity.ps1 pumpclaw          # claim AXM LP fees (any gas payer)
#   .\recover_liquidity.ps1 place-floor-dry   # simulate $1.50+ v4 limit ladder (no broadcast)
#   .\recover_liquidity.ps1 place-floor       # init SINC/USDC hook pool + place floor ladder
#   .\recover_liquidity.ps1 place-ramp        # discovery ramp $0.10-$0.95 on live hook
#   .\recover_liquidity.ps1 deploy-router     # SincHookRouter for USDC buys
#   .\recover_liquidity.ps1 liquidate-nfts    # burn empty v4 LP NFTs on 0xAf9B
#   .\recover_liquidity.ps1 rogue-v2          # remove ~10M SINC from dust Uniswap V2 pair (0x8537…)
#   .\recover_liquidity.ps1 rogue-v2-dry      # print sign-ready calldata only
#   .\hook_fill_keeper.ps1                    # withdraw USDC from filled hook orders

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("pumpclaw", "bcow", "fjord", "fjord-wait", "aerodrome", "aero-exit", "calldata", "creator", "status", "sweep", "place-floor", "place-floor-dry", "place-ramp", "deploy-router", "liquidate-nfts", "rogue-v2", "rogue-v2-dry")]
    [string]$Action,

    [int]$PollSeconds = 60,

    [switch]$PlaceFloorAfter
)

$ErrorActionPreference = "Stop"
$cast = "$env:USERPROFILE\.foundry\bin\cast.exe"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$envFile = Join-Path $here ".env"

if (-not (Test-Path $envFile)) {
    $fallback = "C:\Users\cjay4\OneDrive\Desktop\sincor-clean\onchain\.env"
    if (Test-Path $fallback) { Copy-Item $fallback $envFile -Force }
}
if (-not (Test-Path $envFile)) { throw "Missing onchain/.env (copy from .env.example)" }

$lines = Get-Content $envFile
$rpc = ($lines | Where-Object { $_ -match "^BASE_RPC_URL=(.+)$" } | ForEach-Object { $matches[1] } | Select-Object -First 1)
if (-not $rpc) { $rpc = "https://mainnet.base.org" }
$pk = ($lines | Where-Object { $_ -match "^DEPLOYER_PRIVATE_KEY=(.+)$" } | ForEach-Object { $matches[1] } | Select-Object -First 1)
$treasuryPk = ($lines | Where-Object { $_ -match "^TREASURY_PRIVATE_KEY=(.+)$" } | ForEach-Object { $matches[1] } | Select-Object -First 1)
if (-not $treasuryPk) { $treasuryPk = $pk }
$needsDeployer = @("pumpclaw", "bcow")
if ($needsDeployer -contains $Action -and -not $pk) { throw "DEPLOYER_PRIVATE_KEY missing in .env" }
if (-not $treasuryPk -and @("liquidate-nfts", "fjord", "fjord-wait", "aerodrome", "aero-exit", "place-floor", "place-floor-dry") -contains $Action) {
    throw "TREASURY_PRIVATE_KEY missing in .env"
}

$CREATOR = "0x35cb3bf1b29F81d325EB9A7225a3E87fE7B37D6f"
$TREASURY = "0xAf9B539D8043C634b7E611818518BA7E850F289e"
$LOCKER = "0x9047c0944c843d91951a6C91dc9f3944D826ACA8"
$AXM = "0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822"
$BCOW = "0x6FDdC59f3a84E95685c6874D1Da45B3663b88E95"
$SMART = "0x2a73CCa8010b8A6b67bF86802D295ECf4Cf394b4"
$AERO_NPM = "0xe1f8cd9AC4e4A65F54f38a5CdAfCA44f6dD68b53"
$AERO_POS_ID = "1239124"
$MAX_U128 = "340282366920938463463374607431768211455"
$FJORD = @(
    "0xff38C22C5932Cf4283F33A892763FCCDe2EEa6aD",
    "0xa497DB488e5d6aCCE3CaB5fBe19cB5C63de91959",
    "0x2feDA1347981dCBdcbd249E28B0f5897A5043CCC"
)
$FJORD_SALE_END = 1781262540
$SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
$POOL_MANAGER = "0x498581fF718922c3f8e6A244956aF099B2652b2b"
$HOOK = "0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0"
$USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
$LADDER_SINC_ATOMS = [uint64]2000000000000000  # 20M SINC @ 8 decimals
$ROGUE_V2_PAIR = "0x85372932f9b151a076815d92cf71a97980ffd667"
$ROGUE_V2_LP_OWNER = "0xAf9B539D8043C634b7E611818518BA7E850F289e"

function Get-CastUint([string]$raw) {
    $s = ($raw -split '\s+')[0]
    if ($s -match "^\d+$") { return [uint64]$s }
    return [uint64]$raw.Trim()
}

function Get-CastDecimal([string]$raw) {
    $s = ($raw -split '\s+')[0]
    return [decimal]$s
}

function Get-Signer([string]$key) {
    return (& $cast wallet address --private-key $key).Trim()
}

function Assert-TreasurySigner([string]$key) {
    $signer = Get-Signer $key
    if ($signer -ine $TREASURY) {
        throw "Signer $signer is not treasury $TREASURY. Set TREASURY_PRIVATE_KEY in onchain/.env"
    }
    return $signer
}

function Show-Calldata {
    $aeroData = (& $cast calldata "collect((uint256,address,uint128,uint128))" "($AERO_POS_ID,$TREASURY,$MAX_U128,$MAX_U128)").Trim()
    $bcowData = (& $cast calldata "exitPool(uint256,uint256[])" "100000000000000000000" "[0,0]").Trim()
    $fjordData = (& $cast calldata "close()").Trim()
    $ts = Get-CastUint (& $cast block latest --rpc-url $rpc -f timestamp)
    $left = [int64]$FJORD_SALE_END - [int64]$ts

    Write-Host "=== 1) Aerodrome Slipstream collect (treasury) ==="
    Write-Host "  Signer: $TREASURY"
    Write-Host "  To:     $AERO_NPM"
    Write-Host "  Data:   $aeroData"
    Write-Host "  Est:    ~0.012 USDC fees on position #$AERO_POS_ID"
    Write-Host ""
    Write-Host "=== 2) Fjord close x3 (treasury, after saleEnd) ==="
    Write-Host "  Signer:    $TREASURY"
    Write-Host "  saleEnd:   $FJORD_SALE_END"
    Write-Host "  now:       $ts"
    Write-Host "  ETA:       ~$([math]::Round($left / 3600, 1)) hours ($left seconds)"
    Write-Host "  Data each: $fjordData"
    foreach ($p in $FJORD) { Write-Host "  To: $p" }
    Write-Host "  Recovers:  ~19.5M canonical SINC total"
    Write-Host ""
    Write-Host "=== 3) BCoW exitPool (Coinbase Smart Wallet) ==="
    Write-Host "  Signer: $SMART (Base app passkey, not in .env)"
    Write-Host "  To:     $BCOW"
    Write-Host "  Data:   $bcowData"
    Write-Host "  Recovers: ~1.39 USDC + 100k legacy SINC; canonical SINC on CSW stays separate"
    Write-Host ""
    Write-Host "Run: .\recover_liquidity.ps1 fjord-wait   (auto-close when ready)"
    Write-Host "     .\recover_liquidity.ps1 aerodrome    (broadcast collect if treasury key set)"
    Write-Host "     .\recover_liquidity.ps1 bcow          (broadcast if CSW key set, else prints above)"
}

function Show-RogueV2Status {
    Write-Host "=== Rogue Uniswap V2 SINC/USDC pair ($ROGUE_V2_PAIR) ==="
    Write-Host "WARNING: Blockscout / MetaMask / Li.Fi aggregators route buys here at fake spot (~`$0.0000001/SINC)."
    Write-Host "Canonical buy paths: bonding curve 0x75dE… or hook router on getsincor.com/sinc only."
    Write-Host ""
    $t0 = (& $cast call $ROGUE_V2_PAIR "token0()(address)" --rpc-url $rpc).Trim()
    $t1 = (& $cast call $ROGUE_V2_PAIR "token1()(address)" --rpc-url $rpc).Trim()
    $res = @(& $cast call $ROGUE_V2_PAIR "getReserves()(uint112,uint112,uint32)" --rpc-url $rpc)
    $supply = (& $cast call $ROGUE_V2_PAIR "totalSupply()(uint256)" --rpc-url $rpc).Trim()
    $lpOwnerBal = (& $cast call $ROGUE_V2_PAIR "balanceOf(address)(uint256)" $ROGUE_V2_LP_OWNER --rpc-url $rpc).Trim()
    Write-Host "token0: $t0"
    Write-Host "token1: $t1"
    Write-Host "reserves: $($res -join ', ')"
    Write-Host "LP totalSupply: $supply | Af9B LP balance: $lpOwnerBal"
    if ((Get-CastDecimal $lpOwnerBal) -gt 0) {
        Write-Host "Af9B controls LP — run rogue-v2 to burn LP and recover SINC before more aggregator arbs."
    } else {
        Write-Host "Af9B holds 0 LP — pair may already be drained or LP moved."
    }
}

function Invoke-RogueV2Exit([bool]$Dry) {
    Show-RogueV2Status
    $lpBal = (& $cast call $ROGUE_V2_PAIR "balanceOf(address)(uint256)" $ROGUE_V2_LP_OWNER --rpc-url $rpc).Trim()
    $lpBal = (Get-CastUint $lpBal).ToString()
    if ((Get-CastDecimal $lpBal) -le 0) { throw "No LP on $ROGUE_V2_LP_OWNER to burn" }

    $xferData = (& $cast calldata "transfer(address,uint256)" $ROGUE_V2_PAIR $lpBal).Trim()
    $burnData = (& $cast calldata "burn(address)" $ROGUE_V2_LP_OWNER).Trim()

    if ($Dry) {
        Write-Host ""
        Write-Host "=== Sign from $ROGUE_V2_LP_OWNER (Account 6 / hardware wallet) ==="
        Write-Host "1/2 transfer LP to pair"
        Write-Host "  To:   $ROGUE_V2_PAIR"
        Write-Host "  Data: $xferData"
        Write-Host "2/2 burn LP -> underlying SINC + USDC"
        Write-Host "  To:   $ROGUE_V2_PAIR"
        Write-Host "  Data: $burnData"
        Write-Host ""
        Write-Host "Recovers ~10M SINC + dust USDC. Kills fake aggregator quotes."
        return
    }

    $signer = Get-Signer $treasuryPk
    if ($signer -ine $ROGUE_V2_LP_OWNER) {
        throw "TREASURY_PRIVATE_KEY is $signer (need $ROGUE_V2_LP_OWNER). Run rogue-v2-dry and sign from Af9B."
    }
    Write-Host "1/2 transfer LP ($lpBal) to pair ..."
    & $cast send $ROGUE_V2_PAIR "transfer(address,uint256)" $ROGUE_V2_PAIR $lpBal --rpc-url $rpc --private-key $treasuryPk
    Write-Host "2/2 burn LP -> $ROGUE_V2_LP_OWNER ..."
    & $cast send $ROGUE_V2_PAIR "burn(address)" $ROGUE_V2_LP_OWNER --rpc-url $rpc --private-key $treasuryPk
    Write-Host "Rogue V2 pair drained. Sweep recovered SINC to Safe 0x2d61752adF5092052Ff7D366a9884823C07Cdaf8 if needed."
}

function Show-Status {
    Write-Host "=== Recovery status (Base) ==="
    $ts = Get-CastUint (& $cast block latest --rpc-url $rpc -f timestamp)
    Write-Host "block.timestamp: $ts (Fjord unlock in $([int64]$FJORD_SALE_END - [int64]$ts)s)"
    & $cast call $LOCKER "getPosition(address)(uint256,address)" $AXM --rpc-url $rpc
    & $cast call $BCOW "balanceOf(address)(uint256)" $SMART --rpc-url $rpc | ForEach-Object { Write-Host "BCoW BPT on smart wallet: $_" }
    foreach ($p in $FJORD) {
        $end = (& $cast call $p "saleEnd()(uint256)" --rpc-url $rpc).Trim()
        $close = (& $cast call $p "canClose()(bool)" --rpc-url $rpc).Trim()
        Write-Host "Fjord $p saleEnd=$end canClose=$close"
    }
    try {
        $owner = (& $cast call $AERO_NPM "ownerOf(uint256)(address)" $AERO_POS_ID --rpc-url $rpc 2>$null).Trim()
        if ($owner) { Write-Host "Aerodrome Slipstream #$AERO_POS_ID owner: $owner" }
        else { Write-Host "Aerodrome Slipstream #${AERO_POS_ID}: burned or not found" }
    } catch {
        Write-Host "Aerodrome Slipstream #${AERO_POS_ID}: burned or not found"
    }
}

function Invoke-FjordClose([string]$key) {
    $signer = Assert-TreasurySigner $key
    $ts = Get-CastUint (& $cast block latest --rpc-url $rpc -f timestamp)
    foreach ($p in $FJORD) {
        $owner = (& $cast call $p "owner()(address)" --rpc-url $rpc).Trim()
        $end = Get-CastUint (& $cast call $p "saleEnd()(uint256)" --rpc-url $rpc)
        $close = (& $cast call $p "canClose()(bool)" --rpc-url $rpc).Trim()
        if ($owner -ine $signer) { throw "Signer $signer is not owner of $p (owner=$owner)" }
        if ($close -ine "true" -and $ts -lt $end) {
            $eta = [DateTimeOffset]::FromUnixTimeSeconds($end).UtcDateTime
            throw "Sale still active on $p until $eta UTC (canClose=$close)"
        }
    }
    foreach ($p in $FJORD) {
        Write-Host "close() on $p ..."
        & $cast send $p "close()" --rpc-url $rpc --private-key $key
    }
}

function Show-CreatorGuide {
    Write-Host "=== Creator unwind (0x35cb3) - needs creator private key ==="
    Write-Host ""
    Write-Host "Uniswap v4 positions WITH liquidity (remove via app.uniswap.org or PositionManager):"
    $active = @(
        @{ id = 2524932; note = "SINC/ETH (fake SINC 0x33cc...)" },
        @{ id = 2524705; note = "ROCKET/legacy SINC" },
        @{ id = 2331601; note = "unknown pair" },
        @{ id = 2265866; note = "USDC/legacy SINC (large)" },
        @{ id = 2174193; note = "unknown pair" }
    )
    foreach ($p in $active) { Write-Host "  #$($p.id) - $($p.note)" }
    Write-Host ""
    Write-Host "Closed v4 (0 liquidity, fee dust only): #2204980 #2204963 #2204960 #2174203 #2174159"
    Write-Host ""
    Write-Host "Balancer BPT (100% of pool):"
    Write-Host "  Token: 0xA13258899e79e9bDd82c72e912F167bfC7a742a7 (SINC-ROCKET)"
    Write-Host "  Balance: 100e18 - exit via Balancer UI or vault exitPool once pool ID known"
    Write-Host ""
    Write-Host "ERC-20 to sweep to treasury $TREASURY after LP exits:"
    Write-Host '  ~2.1M legacy SINC (0x49E3), ~12 canonical SINC, 3023 AXM, ETH dust'
    Write-Host ""
    Write-Host "Local pk.txt controls 0xC184 - NOT the creator wallet. Use the key for 0x35cb3"
}

function Invoke-PlaceFloorLadder([bool]$Dry) {
    Assert-TreasurySigner $treasuryPk | Out-Null
    $bal = Get-CastUint (& $cast call $SINC "balanceOf(address)(uint256)" $TREASURY --rpc-url $rpc)
    Write-Host "=== Floor limit ladder (`$1.50 min, 20M SINC across 6 rungs) ==="
    Write-Host "Treasury SINC balance: $bal (requires >= $LADDER_SINC_ATOMS)"
    if ($bal -lt $LADDER_SINC_ATOMS) {
        throw "Need >= 20M SINC in treasury. Run fjord-wait first or consolidate CSW/creator inventory."
    }
    $forge = "$env:USERPROFILE\.foundry\bin\forge.exe"
    if (-not (Test-Path $forge)) { throw "forge not found at $forge" }
    Push-Location $here
    $broadcast = @()
    if (-not $Dry) { $broadcast = @("--broadcast") }
    Write-Host "Step 1/2: Init SINC/USDC hook pool at discovery price (idempotent) ..."
    & $forge script script/06_InitSincUsdcHookPool.s.sol:InitSincUsdcHookPool --rpc-url $rpc --private-key $treasuryPk @broadcast
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Init pool script failed" }
    Write-Host "Step 2/2: Place sell-side limit ladder via SincLimitOrderHook ..."
    & $forge script script/07_PlaceFloorLimitLadder.s.sol:PlaceFloorLimitLadder --rpc-url $rpc --private-key $treasuryPk @broadcast
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { throw "Place ladder script failed" }
    if ($Dry) {
        Write-Host "Dry run OK. Re-run: .\recover_liquidity.ps1 place-floor"
    } else {
        Write-Host "Floor ladder live. Buyers must lift orders at >= `$1.50/SINC."
    }
}

switch ($Action) {
    "sweep" {
        $sweep = Join-Path $here "sweep_wallets.ps1"
        if (-not (Test-Path $sweep)) { throw "Missing sweep_wallets.ps1" }
        & $sweep
        break
    }

    "calldata" { Show-Calldata; break }

    "status" {
        Show-Status
        Write-Host ""
        Show-RogueV2Status
        break
    }

    "rogue-v2-dry" { Invoke-RogueV2Exit $true; break }

    "rogue-v2" { Invoke-RogueV2Exit $false; break }

    "creator" { Show-CreatorGuide; break }

    "aerodrome" {
        $owner = (& $cast call $AERO_NPM "ownerOf(uint256)(address)" $AERO_POS_ID --rpc-url $rpc).Trim()
        if ($owner -ine $TREASURY) { throw "Aerodrome #$AERO_POS_ID owner is $owner, expected $TREASURY" }
        $signer = Assert-TreasurySigner $treasuryPk
        Write-Host "Collecting Aerodrome fees on #$AERO_POS_ID ..."
        & $cast send $AERO_NPM "collect((uint256,address,uint128,uint128))" "($AERO_POS_ID,$TREASURY,$MAX_U128,$MAX_U128)" --rpc-url $rpc --private-key $treasuryPk
        break
    }

    "aero-exit" {
        $owner = (& $cast call $AERO_NPM "ownerOf(uint256)(address)" $AERO_POS_ID --rpc-url $rpc).Trim()
        if ($owner -ine $TREASURY) { throw "Aerodrome #$AERO_POS_ID owner is $owner, expected $TREASURY" }
        $signer = Assert-TreasurySigner $treasuryPk
        $posLines = @(& $cast call $AERO_NPM "positions(uint256)(uint96,address,address,address,int24,int24,uint128,uint256,uint256,uint128,uint128)" $AERO_POS_ID --rpc-url $rpc)
        $liq = ($posLines[7] -split '\s+')[0]
        if (-not $liq) { throw "Could not read position liquidity" }
        $deadline = [int64](& $cast block latest --rpc-url $rpc -f timestamp) + 3600
        Write-Host "Signer: $signer | position #$AERO_POS_ID liquidity=$liq"
        Write-Host "Simulated exit: ~1.2 USDC + ~100k SINC back to treasury (you own 100% of pool)"
        Write-Host "1/3 decreaseLiquidity ..."
        & $cast send $AERO_NPM "decreaseLiquidity((uint256,uint128,uint256,uint256,uint256))" "($AERO_POS_ID,$liq,0,0,$deadline)" --rpc-url $rpc --private-key $treasuryPk
        Write-Host "2/3 collect ..."
        & $cast send $AERO_NPM "collect((uint256,address,uint128,uint128))" "($AERO_POS_ID,$TREASURY,$MAX_U128,$MAX_U128)" --rpc-url $rpc --private-key $treasuryPk
        Write-Host "3/3 burn NFT ..."
        & $cast send $AERO_NPM "burn(uint256)" $AERO_POS_ID --rpc-url $rpc --private-key $treasuryPk
        Write-Host "Aerodrome position eliminated. Do NOT re-add to same ticks - pool had ~`$1.20 USDC depth."
        break
    }

    "fjord-wait" {
        Write-Host "Waiting for Fjord saleEnd=$FJORD_SALE_END (poll every ${PollSeconds}s) ..."
        while ($true) {
            $ts = Get-CastUint (& $cast block latest --rpc-url $rpc -f timestamp)
            $allReady = $true
            foreach ($p in $FJORD) {
                $close = (& $cast call $p "canClose()(bool)" --rpc-url $rpc).Trim()
                if ($close -ine "true" -and $ts -lt $FJORD_SALE_END) { $allReady = $false }
            }
            if ($allReady) { break }
            $left = [int64]$FJORD_SALE_END - [int64]$ts
            Write-Host "$(Get-Date -Format o) timestamp=$ts unlock_in=${left}s canClose pending..."
            Start-Sleep -Seconds $PollSeconds
        }
        Write-Host "Sale ended - closing pools ..."
        Invoke-FjordClose $treasuryPk
        if ($PlaceFloorAfter) {
            Write-Host "Fjord closed - placing `$1.50+ floor ladder ..."
            Invoke-PlaceFloorLadder $false
        }
        break
    }

    "pumpclaw" {
        Write-Host "Broadcasting claimFees($AXM) ..."
        & $cast send $LOCKER "claimFees(address)" $AXM --rpc-url $rpc --private-key $pk
        Write-Host "Fees route to PumpClaw creator $CREATOR"
        break
    }

    "bcow" {
        $bpt = "100000000000000000000"
        $calldata = (& $cast calldata "exitPool(uint256,uint256[])" $bpt "[0,0]").Trim()
        $signer = Get-Signer $pk
        $held = (& $cast call $BCOW "balanceOf(address)(uint256)" $signer --rpc-url $rpc).Trim()
        Write-Host "BCoW pool: $BCOW"
        Write-Host "Smart wallet (BPT holder): $SMART"
        Write-Host "Deployer BPT balance: $held"
        if ($signer -ieq $SMART -or ([decimal]$held -ge 100000000000000000000)) {
            Write-Host "Broadcasting exitPool from $signer ..."
            & $cast send $BCOW "exitPool(uint256,uint256[])" $bpt "[0,0]" --rpc-url $rpc --private-key $pk
        } else {
            Write-Host ""
            Write-Host "Sign from Coinbase Smart Wallet $SMART (Base app -> send transaction):"
            Write-Host "  To:   $BCOW"
            Write-Host "  Data: $calldata"
            Write-Host "Recovers ~1 USDC + 100k legacy SINC. Sweep ~10.4M canonical SINC to treasury after."
            throw "No local key controls smart wallet - use calldata above in Base app"
        }
        break
    }

    "fjord" {
        Invoke-FjordClose $treasuryPk
        break
    }

    "place-floor" {
        $dry = $false
        Invoke-PlaceFloorLadder $dry
        break
    }

    "place-floor-dry" {
        $dry = $true
        Invoke-PlaceFloorLadder $dry
        break
    }

    "place-ramp" {
        Push-Location $here
        try {
            & "$env:USERPROFILE\.foundry\bin\forge.exe" script script/12_PlaceDiscoveryRamp.s.sol --rpc-url $rpc --broadcast --slow
        } finally { Pop-Location }
        break
    }

    "deploy-router" {
        Push-Location $here
        try {
            & "$env:USERPROFILE\.foundry\bin\forge.exe" script script/14_DeployHookRouter.s.sol --rpc-url $rpc --broadcast --slow
        } finally { Pop-Location }
        break
    }

    "liquidate-nfts" {
        $V4PM = "0x7C5f5A4bBd8fD63184577525326123B519429bDc"
        $SAFE = "0x2d61752adF5092052Ff7D366a9884823C07Cdaf8"
        $v4Ids = @(2439297, 2496724, 2496782, 2496822, 2496876)
        $signer = Get-Signer $treasuryPk
        if ($signer -ine $TREASURY) {
            Write-Host "=== Cannot auto-broadcast: TREASURY_PRIVATE_KEY is $signer (need $TREASURY) ==="
            Write-Host "Sign the txs below from Account 6 / hardware wallet, or set the key for 0xAf9B."
            Write-Host ""
            Write-Host "Option A — burn empty shells in Uniswap (recommended, recovers fee dust):"
            Write-Host "  https://app.uniswap.org/positions -> connect 0xAf9B -> close each position"
            Write-Host ""
            Write-Host "Option B — transfer NFT shells to safe (cleanup only, still empty):"
            foreach ($id in $v4Ids) {
                $data = (& $cast calldata "safeTransferFrom(address,address,uint256)" $TREASURY $SAFE $id).Trim()
                Write-Host "  token #$id"
                Write-Host "    To:   $V4PM"
                Write-Host "    Data: $data"
            }
            Write-Host ""
            Write-Host "Genesis NFT #3 is soulbound — cannot transfer."
            Write-Host "Aerodrome Slipstream: 0 NFTs on-chain (wallet UI is stale)."
            Write-Host ""
            Write-Host "Option C — add 0xAf9B key as TREASURY_PRIVATE_KEY, then re-run liquidate-nfts"
            break
        }
        Write-Host "=== Burn 5 empty Uniswap v4 LP NFTs on treasury (fee dust -> safe) ==="
        Write-Host "Skips soulbound Genesis NFT #3. Aerodrome Slipstream: 0 NFTs on-chain."
        Push-Location $here
        try {
            & "$env:USERPROFILE\.foundry\bin\forge.exe" script script/17_LiquidateTreasuryNfts.s.sol:LiquidateTreasuryNfts --rpc-url $rpc --broadcast --slow -vvv
        } finally { Pop-Location }
        break
    }
}