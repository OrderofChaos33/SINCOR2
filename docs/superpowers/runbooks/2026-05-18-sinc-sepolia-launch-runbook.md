# SINC Sepolia Launch Runbook (Plan 1 Tasks 20–22)

**Date:** 2026-05-18
**Branch:** `feat/sinc-onchain`
**Scope:** Execute Plan 1's final user-driven steps — Sepolia deploy, smoke test, CertiK Skynet scans. Mainnet is explicitly out of scope.

Everything above Task 20 is already committed (Tasks 1–19, ending at commit `0f8afcdd`). This doc replaces re-reading the plan; just follow it top-to-bottom.

---

## Prerequisites (one-time)

You need:

1. **A fresh Sepolia-only hot wallet** — generate via MetaMask "Add Account". Do NOT reuse `0xAf9B539D8043C634b7E611818518BA7E850F289e` (treasury). Do NOT reuse any wallet whose key was ever in `pk.txt`, `pk2.txt`, `Axiom1.txt`, etc. This key will be in plaintext in `onchain/.env`, so it must hold nothing you can't afford to lose.
2. **0.05 Base Sepolia ETH** in that fresh wallet. Faucets: https://www.coinbase.com/faucets/base-ethereum-sepolia-faucet or https://www.alchemy.com/faucets/base-sepolia.
3. **Alchemy/QuickNode key for Base** — free tier is fine. One URL for mainnet (used by Integration test), one for Sepolia.
4. **Basescan API key** — free, sign up at https://basescan.org/apis. Needed for `--verify`.
5. **V4 Base Sepolia addresses** — look up at https://docs.uniswap.org/contracts/v4/deployments under "Base Sepolia (84532)". You need `PoolManager` and `PositionManager`. The mainnet values in `Integration.t.sol` will NOT work on Sepolia.

---

## Step 0 — Populate `.env`

```bash
cd C:/Users/cjay4/OneDrive/Desktop/sincor-clean/onchain
cp .env.example .env
```

Edit `onchain/.env`:

```
BASE_RPC_URL=https://base-mainnet.g.alchemy.com/v2/<your-key>
BASE_SEPOLIA_RPC_URL=https://base-sepolia.g.alchemy.com/v2/<your-key>
BASESCAN_API_KEY=<your-basescan-key>
DEPLOYER_PRIVATE_KEY=0x<sepolia-only-hot-wallet-key>

# V4 Base Sepolia — fill from https://docs.uniswap.org/contracts/v4/deployments
POOL_MANAGER=0x...
POSITION_MANAGER=0x...

# Sepolia treasury can be your deployer address — Sepolia only, not production.
TREASURY=<your-deployer-address>

# Filled in by subsequent steps:
SINC_TOKEN=
GENESIS_NFT=
CURVE=
HOOK_SALT=
```

**Confirm `.env` is gitignored before continuing:**

```bash
git check-ignore onchain/.env
# Expected output: onchain/.env
```

If it does NOT print `onchain/.env`, stop and add it to `.gitignore` first.

---

## Step 1 — Run the full local test suite

```bash
cd C:/Users/cjay4/OneDrive/Desktop/sincor-clean/onchain
forge test -vvv
```

Expected: all unit tests pass. Fuzz runs ~1000 iterations per fuzz test (per `foundry.toml`).

Then the fork integration test:

```bash
forge test --match-contract IntegrationTest --fork-url base -vvv
```

Expected: `test_FullPath_BuyGraduateSwap` passes.

**If it fails on `graduate()`:** likely the V4 pool-init `sqrtPriceX96` precision needs a tweak. Plan §2099 flags this. Capture the revert reason, adjust `_computeSqrtPriceX96` in `src/SincBondingCurve.sol` by ±0.5%, re-run. Acceptable iteration loop.

**Do not proceed to Step 2 until both passes.**

---

## Step 2 — Deploy MockSinc to Sepolia (Plan 1 Task 20 Step 2)

MockSinc is the Sepolia testnet stand-in for the canonical SINC token on mainnet. It mints 100M to the deployer.

```bash
forge script script/00_DeployMockSinc.s.sol --rpc-url sepolia --broadcast --verify
```

Expected console output: `MockSinc deployed at: 0x...`

Copy that address into `onchain/.env` as `SINC_TOKEN=0x...`.

---

## Step 3 — Deploy Genesis NFT to Sepolia (Task 20 Step 3)

```bash
forge script script/01_DeployGenesisNFT.s.sol --rpc-url sepolia --broadcast --verify
```

The script prints both `Predicted curve address` and `SincGenesisNFT deployed at`. Copy the NFT address into `.env` as `GENESIS_NFT=0x...`.

The NFT's immutable `curve` is set to the address that the *next* nonce of your deployer will produce. So **Step 4 must use the same deployer wallet with no other deploys in between.**

---

## Step 4 — Deploy Bonding Curve to Sepolia (Task 20 Step 4)

Confirm `.env` has `SINC_TOKEN`, `GENESIS_NFT`, `TREASURY`, `POOL_MANAGER`, `POSITION_MANAGER` all populated.

```bash
forge script script/02_DeployBondingCurve.s.sol --rpc-url sepolia --broadcast --verify
```

Expected: `SincBondingCurve deployed at: 0x...`. Copy into `.env` as `CURVE=0x...`.

**Sanity check:** the printed curve address should match the `Predicted curve address` from Step 3. If it doesn't, the NFT will reject all curve calls. Stop and investigate — likely a different wallet was used or extra deploys happened in between.

---

## Step 5 — Fund the curve with 65M MockSinc (Task 20 Step 5)

```bash
forge script script/03_FundCurveWithSINC.s.sol --rpc-url sepolia --broadcast
```

Expected: `Funded curve with 65M SINC`. Verify on Basescan-Sepolia that the curve contract's MockSinc balance is `6500000000000000` (65M × 10⁸).

---

## Step 6 — Mine the hook salt + deploy the hook (Task 20 Step 6)

```bash
forge script script/04_MineHookAddress.s.sol --rpc-url sepolia
```

Expected output: `Found salt at iteration N` plus a `bytes32` salt. Copy the salt into `.env` as `HOOK_SALT=0x...`.

```bash
forge script script/05_DeployHook.s.sol --rpc-url sepolia --broadcast --verify
```

Expected: `SincLimitOrderHook deployed at: 0x...` AND `Hook wired into curve`. If the second line is missing, the curve's `setHook()` call failed — investigate before proceeding.

---

## Step 7 — Verify deployments file (Task 20 Step 7)

```bash
cat onchain/deployments/84532.json
```

Expected: all three addresses non-zero (`nft`, `curve`, `hook`).

---

## Step 8 — Commit the new scripts + deployment record (Task 20 Step 8)

```bash
cd C:/Users/cjay4/OneDrive/Desktop/sincor-clean
git add onchain/script/00_DeployMockSinc.s.sol onchain/script/SmokeTest.s.sol
git commit -m "onchain: Sepolia MockSinc deploy script + smoke test script"
```

Note: `deployments/84532.json` is gitignored (it contains your live Sepolia addresses but isn't reproducibility-critical for the repo). If you want a record in-repo, copy the values into a comment in the runbook or into a separate `deployments-sepolia.snapshot.md`.

---

## Step 9 — Run the Sepolia smoke test (Task 21)

```bash
cd C:/Users/cjay4/OneDrive/Desktop/sincor-clean/onchain
forge script script/SmokeTest.s.sol --rpc-url sepolia --broadcast
```

Expected console output:
- `Pre-buy: NFT balance: 0`
- `Pre-buy: current price (wei per 1 SINC): <some positive number>`
- `Post-buy: NFT balance: 1`
- `Post-buy: SINC sold: <non-zero>`
- `Post-buy: ETH accumulated: 1000000000000000` (= 0.001 ETH in wei)

---

## Step 10 — Manual Basescan-Sepolia verification (Task 21 Step 3)

Visit each:

- `https://sepolia.basescan.org/address/<CURVE>` — confirm green checkmark (verified source), buy tx visible in Transactions, Read tab shows `sincSold > 0` and `ethAccumulated == 1000000000000000`.
- `https://sepolia.basescan.org/address/<GENESIS_NFT>` — confirm Mint tx visible. Read tab → `ownerOf(1)` returns your deployer address. Try to transfer the NFT from one wallet to another via Basescan Write tab → MUST revert with "Soulbound: non-transferable".
- `https://sepolia.basescan.org/address/<HOOK>` — confirm verified source.

---

## Step 11 — Submit CertiK Skynet scans (Task 22)

For each of the three contracts (NFT, curve, hook):

1. Go to https://skynet.certik.com
2. "Submit a Project" → "Token Scan"
3. Network: Base Sepolia
4. Contract address: paste from `deployments/84532.json`
5. Submit and wait. Typically minutes to a few hours per scan.

**Gate:** Each contract must score ≥90/100. If any scores below 90, read the findings, fix the source, redeploy, re-scan. **Do not move to Plan 4 (mainnet) until all three are ≥90.**

---

## Step 12 — Document scan results

Once scans return, edit `onchain/deployments/84532.json` (locally — still gitignored, but a record for you):

```json
{
  "nft": "0x...",
  "curve": "0x...",
  "hook": "0x...",
  "certik": {
    "nft_score": 0,
    "nft_scan_url": "",
    "curve_score": 0,
    "curve_scan_url": "",
    "hook_score": 0,
    "hook_scan_url": ""
  }
}
```

Fill in the actual values.

---

## Step 13 — Final commit (Plan 1 closeout)

Append a row to this runbook documenting actual scan scores (or create `docs/superpowers/runbooks/2026-05-18-sinc-sepolia-results.md` if you prefer), then:

```bash
cd C:/Users/cjay4/OneDrive/Desktop/sincor-clean
git add docs/superpowers/runbooks/
git commit -m "docs: Plan 1 Sepolia results — CertiK scores recorded"
```

---

## Plan 1 acceptance check

When all of the following are true, Plan 1 is done and you're cleared to start Plan 2 (off-chain Flask/templates) and eventually Plan 4 (mainnet deploy):

- [ ] `forge test -vvv` 100% green locally
- [ ] `forge test --fork-url base -vvv` integration test passes
- [ ] NFT + curve + hook deployed to Base Sepolia, all verified on Basescan
- [ ] Smoke test produced 1 NFT mint + non-zero curve state
- [ ] CertiK Skynet: NFT ≥90, curve ≥90, hook ≥90
- [ ] All commits live on `feat/sinc-onchain` branch (not yet pushed to remote — that's your call)
- [ ] `.env` and `deployments/84532.json` remain gitignored

---

## Hard safety reminders

- **Never paste `DEPLOYER_PRIVATE_KEY` into a chat with me, ever.** If you do, that wallet becomes burnable. Same rule as we established with `0x6E36…` on 2026-05-16.
- **Never put a wallet that has held any real ETH into `.env`.** Use the fresh Sepolia-only hot wallet for testnet, and a hardware wallet for mainnet deploys later.
- **The Sepolia deployer wallet's only job is to deploy testnet contracts.** After Plan 1, abandon it.
- **Mainnet is not in this runbook.** When you're ready for mainnet (Plan 4), it gets its own runbook with hardware-wallet-signed transactions, no `.env` keys, and the canonical SINC token at `0x9C8cd8d3961F445D653713dE65C6578bE11668e7`.
