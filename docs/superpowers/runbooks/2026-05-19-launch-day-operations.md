# SINC Launch Day Operations — 20-Hour Runbook

**Launch:** 2026-05-20 ~21:00 local (~T-20h from drafting)
**Status as of 2026-05-19:** 32/32 unit tests green. Sepolia + mainnet deploys not yet run. CertiK scans not yet submitted. Content kit drafts at `outputs/launch_content_kit_DRAFT.md`.
**Branch:** `feat/sinc-onchain`

This doc is the single page you operate from for the next 20 hours. It supersedes the time-sensitive sections of [`2026-05-18-sinc-sepolia-launch-runbook.md`](2026-05-18-sinc-sepolia-launch-runbook.md) and adds the mainnet step that runbook deferred.

---

## Time budget (with slack)

| Block | Hours | What happens |
|---|---|---|
| **H+0 → H+1** | 1h | Fresh hot wallet, Sepolia faucet, V4 address lookup, populate `.env` |
| **H+1 → H+2** | 1h | 5 Sepolia deploy scripts + funding + hook |
| **H+2 → H+3** | 1h | Smoke test + Basescan-Sepolia verification |
| **H+3 → H+4** | 1h | Submit CertiK Skynet on NFT + curve + hook |
| **H+4 → H+10** | 6h | CertiK wait (typical 2-6h; worst case 24h — if this slips past H+12, decision point on launching without scan) |
| **H+10 → H+12** | 2h | Image assets, Railway env vars confirm, content final review |
| **H+12 → H+14** | 2h | **Mainnet deploy with hardware wallet** (see §6) |
| **H+14 → H+15** | 1h | `/sinc` page final check, Dune dashboard live, CertiK badge embed |
| **H+15 (≈T-6h)** | — | Teaser thread on X |
| **H+17 (≈T-3h)** | — | Teaser cast on Farcaster, Telegram pin |
| **H+18 (≈T-2h)** | — | KOL DMs sent |
| **H+19.5 (≈T-30m)** | — | Launch thread initialized |
| **H+20 (≈T-0)** | — | Hero thread published across all channels |

**Hard gate at H+12:** If CertiK has not returned ≥90 on all three contracts by H+12, the launch decision is yours. Options: (a) push 24h and wait, (b) launch with disclosure that scans are in progress.

---

## 0. RPC URLs (already in `.env`)

`onchain/.env` now has:
- `BASE_RPC_URL` = your Alchemy Base mainnet URL
- `BASE_SEPOLIA_RPC_URL` = your Alchemy Base Sepolia URL

You don't need to re-paste these. They're gitignored.

---

## 1. Generate fresh Sepolia-only hot wallet (~30 sec)

**Do NOT reuse `0xAf9B539D8043C634b7E611818518BA7E850F289e` or any wallet that ever held real ETH.** This wallet's only job is Sepolia deploys. After launch, abandon it.

```powershell
cd C:\Users\cjay4\OneDrive\Desktop\sincor-clean\onchain
& "$env:USERPROFILE\.foundry\bin\cast.exe" wallet new
```

Output looks like:
```
Successfully created new keypair.
Address:     0x...
Private key: 0x...
```

**Paste the private key (with `0x` prefix) into `onchain/.env` as `DEPLOYER_PRIVATE_KEY=0x...`.**
**Paste the address into `.env` as `TREASURY=0x...`** (Sepolia treasury = deployer; this is fine for testnet only).

⚠️ The private key now lives in plaintext in `.env`. Do not screenshot it, do not paste it in any chat, do not commit it. `.env` is gitignored — verify: `git check-ignore onchain/.env` should print `onchain/.env`.

---

## 2. Get free Sepolia ETH (~5 min)

You need ~0.05 Base Sepolia ETH. Faucets (paste your Sepolia address):

1. https://www.coinbase.com/faucets/base-ethereum-sepolia-faucet (1 drip / 24h, 0.05 ETH)
2. https://www.alchemy.com/faucets/base-sepolia (free with Alchemy account, which you already have)
3. https://www.quicknode.com/faucets/base-sepolia (backup)

Confirm balance: `cast balance <your-address> --rpc-url sepolia` (must show > 50000000000000000 wei = 0.05 ETH).

---

## 3. Look up V4 Base Sepolia addresses (~5 min)

Go to https://docs.uniswap.org/contracts/v4/deployments → scroll to **Base Sepolia (84532)**. Copy:
- `PoolManager` address
- `PositionManager` address

Paste both into `onchain/.env`:
```
POOL_MANAGER=0x...
POSITION_MANAGER=0x...
```

---

## 4. Run Sepolia deploys (~10 min total)

Each command broadcasts a transaction signed by `DEPLOYER_PRIVATE_KEY`. Run them **in order, same shell session**. The NFT-curve nonce dance (steps 4.2 → 4.3) requires no other deploys between them.

```powershell
cd C:\Users\cjay4\OneDrive\Desktop\sincor-clean\onchain
$forge = "$env:USERPROFILE\.foundry\bin\forge.exe"
```

**4.1 — MockSinc (Sepolia stand-in for canonical SINC):**
```powershell
& $forge script script/00_DeployMockSinc.s.sol --rpc-url sepolia --broadcast --verify
```
Copy printed address → `.env` as `SINC_TOKEN=0x...`

**4.2 — Genesis NFT:**
```powershell
& $forge script script/01_DeployGenesisNFT.s.sol --rpc-url sepolia --broadcast --verify
```
Copy NFT address → `.env` as `GENESIS_NFT=0x...`. Note "Predicted curve address" — step 4.3 MUST match it.

**4.3 — Bonding Curve:**
```powershell
& $forge script script/02_DeployBondingCurve.s.sol --rpc-url sepolia --broadcast --verify
```
Copy curve address → `.env` as `CURVE=0x...`. **Must match the predicted address from 4.2.** If it doesn't, stop — another tx slipped in between.

**4.4 — Fund curve with 65M SINC:**
```powershell
& $forge script script/03_FundCurveWithSINC.s.sol --rpc-url sepolia --broadcast
```

**4.5 — Mine hook salt + deploy hook:**
```powershell
& $forge script script/04_MineHookAddress.s.sol --rpc-url sepolia
```
Copy salt → `.env` as `HOOK_SALT=0x...`

```powershell
& $forge script script/05_DeployHook.s.sol --rpc-url sepolia --broadcast --verify
```
Confirm `Hook wired into curve` in output.

**4.6 — Sanity check:**
```powershell
Get-Content onchain\deployments\84532.json
```
All three addresses non-zero. Save the JSON contents somewhere (it's gitignored).

---

## 5. Smoke test + verification (~10 min)

```powershell
& $forge script script/SmokeTest.s.sol --rpc-url sepolia --broadcast
```

Expected output:
- `Post-buy: NFT balance: 1`
- `Post-buy: SINC sold: <non-zero>`
- `Post-buy: ETH accumulated: 1000000000000000`

Visit each Basescan-Sepolia URL and confirm:
- `sepolia.basescan.org/address/<CURVE>` → verified, buy tx visible, Read tab `sincSold > 0`
- `sepolia.basescan.org/address/<GENESIS_NFT>` → mint tx, `ownerOf(1)` = deployer, try transfer via Write tab → MUST revert "Soulbound"
- `sepolia.basescan.org/address/<HOOK>` → verified source

---

## 6. Submit CertiK Skynet scans (~10 min submit, then wait)

For each of NFT, curve, hook:
1. https://skynet.certik.com → "Submit a Project" → "Token Scan"
2. Network: **Base Sepolia**
3. Contract address: from `deployments/84532.json`
4. Submit, note the scan URL

**Gate:** each must score ≥90/100. If any score below 90, read findings, fix source, redeploy, re-scan.

---

## 7. Mainnet deploy (H+12 → H+14, hardware wallet required)

⚠️ **Do NOT use a plaintext-key hot wallet for mainnet.** Mainnet uses your hardware wallet (Ledger / Trezor) via Frame, Rabby, or Foundry's `--ledger` flag.

**7.1 — Switch `.env` to mainnet vars:**
```
# Comment out Sepolia treasury, replace with the real mainnet treasury:
TREASURY=0xAf9B539D8043C634b7E611818518BA7E850F289e   # or whichever address you control with hardware wallet

# V4 Base mainnet (do NOT use Sepolia values):
POOL_MANAGER=0x498581fF718922c3f8e6A244956aF099B2652b2b
POSITION_MANAGER=0x7C5f5A4bBd8fD63184577525326123B519429bDc

# The canonical SINC token already exists on mainnet:
SINC_TOKEN=0x9C8cd8d3961F445D653713dE65C6578bE11668e7

# Leave these blank — populated by the deploys below:
GENESIS_NFT=
CURVE=
HOOK_SALT=

# REMOVE the DEPLOYER_PRIVATE_KEY line — mainnet uses --ledger, no key in .env
DEPLOYER_PRIVATE_KEY=
```

**7.2 — Mainnet deploys with Ledger:**

For each script, replace `--broadcast` with `--ledger --broadcast` and Foundry will prompt your hardware wallet to sign each tx. Run in same order:

```powershell
& $forge script script/01_DeployGenesisNFT.s.sol --rpc-url base --ledger --broadcast --verify
& $forge script script/02_DeployBondingCurve.s.sol --rpc-url base --ledger --broadcast --verify
```

Note: **Skip `00_DeployMockSinc.s.sol`** — canonical SINC at `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` already exists.

**7.3 — Fund the curve with 65M SINC (from your existing SINC holdings):**
The original 100M canonical SINC supply is owned by `0x35cb3bf1b29F81d325EB9A7225a3E87fE7B37D6f`. Per [[signing_authority]] memory, that wallet is the original creator. You sign this transfer with whatever key controls `0x35cb…7D6f`. If you no longer control it, this step blocks — flag it now.

```powershell
& $forge script script/03_FundCurveWithSINC.s.sol --rpc-url base --ledger --broadcast
```

**7.4 — Hook salt mine + deploy:**
```powershell
& $forge script script/04_MineHookAddress.s.sol --rpc-url base
# Copy printed HOOK_SALT into .env
& $forge script script/05_DeployHook.s.sol --rpc-url base --ledger --broadcast --verify
```

**7.5 — Verify mainnet deployments:**
```powershell
Get-Content onchain\deployments\8453.json
```
All three non-zero. Save the JSON for the launch posts.

---

## 8. Pre-launch checklist (H+19.5)

- [ ] `forge test` 32/32 green ✅ (already done)
- [ ] Sepolia deploys + smoke test passed
- [ ] CertiK Skynet ≥90 on NFT + curve + hook
- [ ] Mainnet contracts deployed + verified on Basescan
- [ ] 65M SINC transferred to mainnet curve
- [ ] Curve hook wired (`setHook` call succeeded)
- [ ] CertiK badge embedded on `/sinc`
- [ ] Dune dashboard at `dune.com/sincor/sinc` is live
- [ ] Hero launch thread drafted + Auditor-cleared (see content kit file)
- [ ] KOL DM list locked (5-10 hand-picked)
- [ ] Railway env vars confirmed: Twitter / Farcaster (Neynar) / Telegram bot / Facebook
- [ ] Images uploaded + render on `/sinc`, `/axiom`, `/`
- [ ] You: sober, caffeinated, available H+15 → H+22

---

## 9. T-0 launch sequence

When you confirm graduation tx mined on Base mainnet:
1. **X (Twitter):** publish hero thread (8-10 tweets)
2. **Farcaster:** publish launch cast in `/base` channel
3. **Telegram:** post in SINCOR channel, pin
4. **Facebook:** publish long-form post
5. **T+15min:** first stats tweet (live price, holder count, buy tx hashes)
6. **T+1h:** reply wave (Auditor pre-checks each)
7. **T+2h:** first-hour recap quote-tweet
8. **T+3h:** community-handoff message; launch window closes

---

## 10. Failure modes (quick ref)

| Failure | Action |
|---|---|
| CertiK < 90 on any contract | Read findings → fix Solidity → redeploy → re-scan. Push launch 24h if needed. |
| Sepolia tx reverts on `graduate()` | Per Plan 1 §2099: `_computeSqrtPriceX96` precision; tune ±0.5% in `SincBondingCurve.sol`, redeploy curve. |
| Mainnet gas estimate looks wrong | Stop. Check `--gas-estimate-multiplier` and L1-blob fee. Base mainnet deploys typically $1-3 each. |
| `0x35cb…7D6f` SINC transfer blocks | Mainnet launch cannot proceed without 65M SINC in curve. If you no longer control that wallet, this is a project-stop — flag immediately. |
| Audit DB (DEXTools etc.) flags as scam | Submit appeal with CertiK + Basescan links; 24-72h lead time. Don't retry naively. |

---

## What I (Claude) did vs. what you do

| Done by me, free | Done by you, signing required |
|---|---|
| 32/32 unit tests verified | `cast wallet new` (you control the key) |
| Content kit drafts (`outputs/launch_content_kit_DRAFT.md`) | Faucet → wallet |
| This runbook | All `forge script ... --broadcast` calls |
| Compliance guardrails (already committed `ad7e7902`) | CertiK Skynet submissions |
| Working tree audit (see §11) | All public posts |

---

## 11. Working tree cleanup (~5 min, do before launch commit)

Current uncommitted:
```
modified:   money_assets/PRODUCT_CATALOG.json
modified:   money_assets/ai_agent_bundle.md
modified:   money_assets/growth_forecast_template.md
modified:   money_assets/instant_bi_template_pack.md
deleted:    templates/buy_sinc.html
Untracked:  .railwayignore
Untracked:  docs/superpowers/runbooks/2026-05-20-post-launch-liquidity-acceleration.md
Untracked:  money_assets/instant_bi_template_listing.json
Untracked:  sincor-clean.BAK.20251015-103920/
```

Recommend:
- Commit `money_assets/*`, `.railwayignore`, the post-launch playbook, and `templates/buy_sinc.html` deletion in one tidy pre-launch commit
- **Do NOT** stage `sincor-clean.BAK.20251015-103920/` — that's a backup snapshot, add to `.gitignore` instead

Tell me when you're ready and I'll stage these cleanly.
