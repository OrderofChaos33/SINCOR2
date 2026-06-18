# BaseScan Verification — SincBondingCurve (0x75dE…619C)

Everything needed to verify the live curve on BaseScan. **No private key is involved** — source
verification only publishes source code; it cannot move funds or change the contract.

## Confirmed facts (from on-chain + broadcast/02_DeployBondingCurve.s.sol/8453/run-latest.json)

| Field | Value |
|---|---|
| Contract | `SincBondingCurve` |
| Address | `0x75dE341a2BC81806198364F125d4Cde36527619C` |
| Source file | `onchain/src/SincBondingCurve.sol` |
| Compiler | `v0.8.26` |
| Optimizer | Enabled, **200** runs |
| via-IR | **Yes** (foundry.toml `via_ir = true`) |
| EVM version | `cancun` |
| License | MIT |
| Chain | Base mainnet (8453) |

### Constructor arguments (in order)
1. `sinc`     = `0x9C8cd8d3961F445D653713dE65C6578bE11668e7`  ← canonical CertiK-audited SINC (8 decimals)
2. `treasury` = `0x7B4082f78CdAc2cB5fa8572b2CA54BeDaaa8f956`
3. `nft`      = `0xF3Bd56788b5E56DE638AF5dDffFA478838A68d09`
4. `poolManager`     = `0x498581fF718922c3f8e6A244956aF099B2652b2b` (Uniswap v4 PoolManager, Base)
5. `positionManager` = `0x7C5f5A4bBd8fD63184577525326123B519429bDc` (Uniswap v4 PositionManager, Base)

### ABI-encoded constructor args (paste into BaseScan, WITHOUT a leading 0x)
```
0000000000000000000000009c8cd8d3961f445d653713de65c6578be11668e70000000000000000000000007b4082f78cdac2cb5fa8572b2ca54bedaaa8f956000000000000000000000000f3bd56788b5e56de638af5ddfffa478838a68d09000000000000000000000000498581ff718922c3f8e6a244956af099b2652b2b0000000000000000000000007c5f5a4bbd8fd63184577525326123b519429bdc
```

---

## Route A — forge + BaseScan API key (recommended, fully automated)

Prereq: install Foundry (`forge`), and get a free BaseScan API key from https://basescan.org/myapikey
(Etherscan v2 keys also work — set `BASESCAN_API_KEY`).

Run from the `onchain/` directory (foundry.toml supplies solc 0.8.26, optimizer 200, via_ir, cancun automatically):

```bash
forge verify-contract \
  0x75dE341a2BC81806198364F125d4Cde36527619C \
  src/SincBondingCurve.sol:SincBondingCurve \
  --chain 8453 \
  --watch \
  --etherscan-api-key "$BASESCAN_API_KEY" \
  --constructor-args 0x0000000000000000000000009c8cd8d3961f445d653713de65c6578be11668e70000000000000000000000007b4082f78cdac2cb5fa8572b2ca54bedaaa8f956000000000000000000000000f3bd56788b5e56de638af5ddfffa478838a68d09000000000000000000000000498581ff718922c3f8e6a244956af099b2652b2b0000000000000000000000007c5f5a4bbd8fd63184577525326123b519429bdc
```

## Route B — sourcify, no API key (matches your current foundry.toml setup)

```bash
forge verify-contract \
  0x75dE341a2BC81806198364F125d4Cde36527619C \
  src/SincBondingCurve.sol:SincBondingCurve \
  --chain base \
  --verifier sourcify
```

## Route C — manual on BaseScan (no forge needed, but most fiddly)

The contract imports OpenZeppelin + Uniswap v4, so a single flattened file usually fails with via-IR.
Use **Standard JSON Input** instead:

1. Generate the JSON (needs forge once):
   ```bash
   forge verify-contract 0x75dE341a2BC81806198364F125d4Cde36527619C \
     src/SincBondingCurve.sol:SincBondingCurve --chain 8453 \
     --show-standard-json-input > sinc_curve_stdjson.json
   ```
2. BaseScan → address `0x75dE…619C` → **Contract → Verify and Publish**
3. Compiler type: **Solidity (Standard-JSON-Input)**, version **v0.8.26**, license **MIT**
4. Upload `sinc_curve_stdjson.json`
5. Constructor args: paste the ABI-encoded string above (no `0x`)
6. Submit.

---

## After verification — what buyers will see
- Readable source, the `sinc` address resolving to the canonical audited token, no hidden owner/admin.
- This is the single biggest trust lever for the raise. Do this before driving any traffic.
