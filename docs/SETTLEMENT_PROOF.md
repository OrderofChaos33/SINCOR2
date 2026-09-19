# Settlement proofs

## Loop

See `docs/LOOP_SETTLEMENT.md` for the existing Loop proof.

## x402 / escrow (open)

`#232` added x402-paid healthcare execution in software. A Base mainnet
transaction hash of one live x402 payment through the escrow contract is
**not yet in this repo**.

This file is the slot. When an operator submits a real mainnet payment:

| Field | Value |
|---|---|
| Chain | Base (8453) |
| Rail | x402 → escrow |
| Tx hash | _pending live payment_ |
| Explorer | `https://basescan.org/tx/<hash>` |
| Amount / asset | AXM or USDC |
| Date | |

Do not paste a testnet hash here and call it mainnet proof.
