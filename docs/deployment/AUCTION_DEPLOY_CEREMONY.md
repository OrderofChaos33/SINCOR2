# Auction Deployment Ceremony

Runbook for deploying `CommitRevealAuction` + `ExecutionEscrowManager`.
Script: `scripts/deploy_auction_contracts.py`.

## Standing rules

- Rehearse on Base Sepolia first. Every time. No exceptions for mainnet.
- Deployer key lives in the operator's shell env only (`DEPLOYER_KEY`).
  Never in chat, never in a file, never in the repo.
- Use a freshly generated, gas-only deployer wallet. Retire it after.
- Adjudicator address comes from the operator (`ADJUDICATOR_ADDRESS`).
  It is single-key for now — never represent dispute resolution as
  decentralized.
- Compliance checkpoint prints at every pre-flight; read it every time.

## 1. Prepare

```bash
git checkout xioix/auction-onchain-deploy   # or main, once merged
pip install web3 eth-account py-solc-x
export DEPLOYER_KEY=0x...          # fresh, gas-only wallet
export ADJUDICATOR_ADDRESS=0x...   # operator decision
export BASESCAN_API_KEY=...        # optional, for --verify
```

## 2. Dry run (no network sends)

```bash
python scripts/deploy_auction_contracts.py --sepolia --dry-run
```

Confirm the plan: chain 84532, `minStakeBps=5000`,
`challengerBond=0.02 ETH`.

## 3. Sepolia rehearsal

Fund the deployer with a small amount of Base Sepolia ETH, then:

```bash
python scripts/deploy_auction_contracts.py --sepolia --verify
```

Expected:
- Both contracts deployed and linked (`auction.setEscrowManager`).
- Onchain runtime bytecode matches the local compile byte-for-byte.
- Manifest + artifacts written to
  `onchain/deployments/artifacts/base-84532-auction/`.
- Basescan submission attempted; Sourcify job polled.

Record the two addresses from the printed env block.

## 4. Full lifecycle on Sepolia

With the rehearsal addresses configured (`COMMIT_REVEAL_AUCTION_ADDRESS`,
`EXECUTION_ESCROW_ADDRESS`, `AUCTION_RPC_URL`, `AUCTION_RELAYER_KEY`):

1. Open an auction (relayer or direct contract call).
2. Two bidders commit from their own wallets.
3. Both reveal.
4. `timeout()` / selection → `selectWinnerAndFund` with the Vickrey price.
5. Stake → result → dispute path on `ExecutionEscrowManager`
   (adjudicator resolves, payouts / `withdraw()` pull-fallback).
6. Confirm Sourcify + Basescan show both contracts verified.

Only proceed when every step above is green.

## 5. Mainnet

```bash
python scripts/deploy_auction_contracts.py --i-understand-mainnet --verify
```

- Fund the deployer with just enough ETH for the deploy + linking.
- Keep `AUCTION_ONCHAIN_FUND=0` until funding behavior and the
  AXM-vs-ETH settlement question are explicitly approved.
- Configure the Railway env vars from the script's printed block.

## 6. After

- Commit the deployment manifest + artifacts to the repo
  (addresses and tx hashes are public data).
- Log the deploy in the ops notes with date, chain, addresses,
  and who held the adjudicator key.
- Rotate/retire the deployer wallet.

## Verification artifacts (per deploy)

Under `onchain/deployments/artifacts/base-<chainid>-auction/`:

| File | Purpose |
|---|---|
| `standard-json-input.json` | exact compiler input for re-verification |
| `CommitRevealAuction.abi.json` / `ExecutionEscrowManager.abi.json` | runtime ABIs |
| `CommitRevealAuction.bytecode.txt` / `ExecutionEscrowManager.bytecode.txt` | creation bytecode |
| `manifest.json` | addresses, tx hashes, ABI-encoded constructor args, compiler settings |

These are sufficient to re-verify on Basescan/Sourcify manually if the
automatic submission ever fails.
