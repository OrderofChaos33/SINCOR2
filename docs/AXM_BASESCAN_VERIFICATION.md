# AXM Basescan verification runbook

Live token: `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a` (Base 8453)

`TOKEN_CANON.json` → `axiom.source_verified_basescan` is **false** until the
Basescan checkmark is live. Do not flip the flag from this document.

## Why it is still false (2026-09-19)

The token page has no “Contract Source Code Verified” checkmark. Bytecode
has exact matches against other contracts. `onchain/src/Axiom.sol` is the
intended source but has not been published against this address.

## Submit (operator with deployer key + Etherscan API key)

```bash
cd onchain
forge flatten src/Axiom.sol > /tmp/Axiom.flattened.sol
forge verify-contract \
  --chain-id 8453 \
  --verifier-url https://api.etherscan.io/v2/api \
  --etherscan-api-key "$ETHERSCAN_API_KEY" \
  0x4c3fb66f14fbaa2088c9ae91017ba770da53715a \
  src/Axiom.sol:Axiom
```

If the constructor took arguments, pass `--constructor-args` from the
deployment tx on Basescan. If flatten + verify fail because the on-chain
bytecode is a different compiler/optimizer setting, stop. Do not publish
a mismatch and call it verified.

## After the checkmark is live

1. Confirm https://basescan.org/address/0x4c3fb66f14fbaa2088c9ae91017ba770da53715a#code shows a green checkmark (exact match, not similar).
2. Set `axiom.source_verified_basescan` to `true` in `TOKEN_CANON.json`.
3. Bump `verified_at` and `lock_version`.
