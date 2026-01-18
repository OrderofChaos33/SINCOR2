# Project Secrets & Configuration Summary

## 🔑 Critical Keys & Secrets

### Private Keys (DO NOT SHARE)
- **Deployer / Main Bot Key:** `0x590a22b9c4d798dcc66c39c3cad6aff574738b9792934cb80a03d7b337c916f4`
  - *Used for:* Deploying contracts, running SINC bots, executing liquidations.
  - *Associated Address:* `0xF915f3F4954c3da6A7D76B424b980A897c3909f1` (Safe Wallet)
- **Compromised Key (Old):** `0x1d580c7fc747f69ca4f51a51c06e540a263daa69125ba22e992a0fd2e03bca54`
  - *Status:* DO NOT USE.

### API Keys
- **Basescan API Key:** `S6XK9N5PH8UM9W7QVCI1V2REP4IHRBEEN6`
  - *Used for:* Verifying contracts on BaseScan.

---

## 🌐 Network & RPC Configuration
- **Network:** Base Mainnet
- **Chain ID:** 8453
- **RPC URL:** `https://mainnet.base.org`
  - *Alternative:* `https://1rpc.io/base`
  - *Alternative:* `https://base.publicnode.com`

---

## 📜 Deployed Addresses (Core System)

### SINC Token Ecosystem
- **SINC Token:** `0xd10D86D09ee4316CdD3585fd6486537b7119A073`
- **Bonding Curve:** `0x25cA41Dac29f892c72A53500853eC45a5FfF90aa`
- **AMM Router (Aerodrome):** `0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43`
- **AMM Factory:** `0x420DD381b31aEf6683db6B902084cB0FFECe40Da`
- **Quote Token (WETH):** `0x4200000000000000000000000000000000000006`
- **Fee Recipient:** `0x9c87ac99da9AA1c6A7A20ac8214DCa846870b1c7`

### Liquidation & Arbitrage
- **Flash Liquidation Bot Logic:** `external/sin-bonding-curve/scripts/bots/flash_liquidator_bot.js`
- **Flash Liquidator Contract:** `0x2F4F448581c6722B1B8D8fae08c98C0E3c63DDF9` (Base)
  - *Note:* Check `external/sin-bonding-curve/.env` for latest deployment address references.

---

## 💧 Faucet Information
*(Note: Faucet infrastructure may be for testnet or development use, but relevant addresses found in scripts)*

- **Faucet Contract Script:** `external/sin-bonding-curve/scripts/deploy-faucet.js`
- **Faucet URL:** *No public web URL found in repository.* Access is likely via direct contract interaction or local scripts.
- **To Deploy Faucet:**
  ```bash
  cd external/sin-bonding-curve
  npx hardhat run scripts/deploy-faucet.js --network base
  ```

---

## 🤖 Operational Commands (From `operations/` folder)

1. **Run SINC Value Maintainer:**
   `operations\run_sinc_value_bot.bat`

2. **Run Flash Liquidator:**
   `operations\run_flash_liquidator.bat`

---

## ⚠️ Important Must-Remembers
1. **Never commit `.env` files** containing real keys to public repositories.
2. **Monitor Gas Funds:** Ensure `0xF915f3...` always has ETH on Base for transaction fees.
3. **Safety First:** The address `0xF915...` is the "Safe Wallet". Use this preferentially over old keys.
4. **Liquidation Risk:** Flash loan bots require no capital but failed transactions burn gas. Monitor `flash_liquidator_bot.js` logs.
