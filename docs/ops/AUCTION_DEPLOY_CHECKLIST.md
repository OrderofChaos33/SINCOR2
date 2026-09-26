# Auction Contracts — Deploy Checklist

Status: **NOT DEPLOYED.** `CommitRevealAuction` and `ExecutionEscrowManager`
compile (solc 0.8.24, `--via-ir --optimize --optimize-runs 200`) and their
ABIs are vendored at `src/sincor2/onchain/abis/`. Python wiring exists as
unsigned-tx builders in `src/sincor2/onchain/auction_client.py`. No mainnet or
testnet deployment has happened.

Deploy order is **Sepolia first, then Base mainnet**. Do not skip Sepolia.

## 0. Prerequisites

- [ ] **Foundry installed.** Not present on the build machine — install:
  `curl -L https://foundry.paradigm.xyz | bash && foundryup`.
  (The repo also compiles with the local solc 0.8.24 binary at
  `~/.solcx/solc-v0.8.24`, but Foundry is the deploy/verify path.)
- [ ] **Deployer private key in the Secure Vault.** NEVER paste it in chat,
  never put it in a file, never export it to shell history. Foundry reads it
  via `--account` / keystore, or `cast wallet import`. The Python client
  (`auction_client.py`) deliberately never accepts key material.
- [ ] **RPC URLs:** Base Sepolia + Base mainnet endpoints (e.g. Alchemy/
  Infura or `https://mainnet.base.org` / `https://sepolia.base.org`).
- [ ] **Basescan API key** for `forge verify-contract` (Base + Base Sepolia).
- [ ] **Gas funds:** Sepolia ETH for testnet, real ETH on Base for mainnet.
- [ ] **Adjudicator address decided** (see warnings — this is THE blocker).
- [ ] Ratified params confirmed: `minStakeBps = 5000` (50% of bid value),
  `challengerBond = 0.02 ETH` (20000000000000000 wei).

## 1. Compile (reproducible)

```bash
cd ~/workspace/sincor2
forge build --via-ir --optimize --optimizer-runs 200
# NOTE: --via-ir is REQUIRED — plain compile fails with "stack too deep".
```

Sanity: `forge test` on the existing suites
(`tests/pytest/test_execution_escrow.py`, `test_selection_bridge.py` —
48/48 passing as of 2026-09-25).

## 2. Deploy to Base Sepolia (chain ID 84532)

```bash
export RPC_URL="https://sepolia.base.org"   # or your provider URL

# 1. Auction core (no constructor args; deployer becomes owner)
AUCTION=$(forge create contracts/CommitRevealAuction.sol:CommitRevealAuction \
  --rpc-url $RPC_URL --account deployer --via-ir \
  --broadcast | grep "Deployed to" | awk '{print $3}')

# 2. Escrow manager: (auctionCore, adjudicator, minStakeBps, challengerBond)
ESCROW=$(forge create contracts/ExecutionEscrowManager.sol:ExecutionEscrowManager \
  --constructor-args $AUCTION <ADJUDICATOR_ADDRESS> 5000 20000000000000000 \
  --rpc-url $RPC_URL --account deployer --via-ir \
  --broadcast | grep "Deployed to" | awk '{print $3}')

# 3. Point the auction core at the escrow manager (owner-only)
cast send $AUCTION "setEscrowManager(address)" $ESCROW \
  --rpc-url $RPC_URL --account deployer
```

## 3. Verify on Basescan (Sepolia)

```bash
forge verify-contract $AUCTION contracts/CommitRevealAuction.sol:CommitRevealAuction \
  --chain-id 84532 --etherscan-api-key $BASESCAN_KEY --via-ir \
  --compiler-version v0.8.24 --num-of-optimizations 200
forge verify-contract $ESCROW contracts/ExecutionEscrowManager.sol:ExecutionEscrowManager \
  --chain-id 84532 --etherscan-api-key $BASESCAN_KEY --via-ir \
  --compiler-version v0.8.24 --num-of-optimizations 200 \
  --constructor-args $(cast abi-encode "constructor(address,address,uint256,uint256)" \
    $AUCTION <ADJUDICATOR_ADDRESS> 5000 20000000000000000)
```

## 4. Sepolia wiring smoke test (Python, no keys in repo)

```bash
export COMMIT_REVEAL_AUCTION_ADDRESS=$AUCTION
export EXECUTION_ESCROW_ADDRESS=$ESCROW
export AUCTION_RPC_URL=$RPC_URL
export AUCTION_CHAIN_ID=84532
```

Exercise the full lifecycle through `auction_client.py` with throwaway
testnet keys held ONLY in the operator's local signer:

1. `openAuction(auctionId, 0, 0)` → `AuctionOpened`
2. `build_commit_tx` → commit → `Committed`
3. wait out commit window → `build_reveal_tx` → `Revealed`
4. `timeout(auctionId)` after reveal window (permissionless)
5. `selectWinnerAndFund` with `value` = Vickrey price → `WinnerSelected`
6. `initializeEscrow` → `depositStake` (exact stake) → `submitResult` →
   `withdraw` → `Withdrawn`
7. `get_past_events("escrow", "SlashExecuted")` — expect none on happy path.

Only proceed to mainnet when this passes end-to-end on Sepolia.

## 5. Deploy to Base mainnet (chain 8453)

Repeat steps 2–3 with the mainnet RPC, `--chain-id 8453`, and real funds.
Then:

- [ ] Record both addresses in `CANONICAL_ADDRESSES.md`.
- [ ] Set `COMMIT_REVEAL_AUCTION_ADDRESS` / `EXECUTION_ESCROW_ADDRESS` /
  `AUCTION_RPC_URL` on the production host (Railway env vars).
- [ ] Start the Basescan watcher for `WinnerSelected` / `EscrowFinalized` /
  `SlashExecuted` (platform-fee ledger signal).
- [ ] Announce the adjudicator arrangement publicly (see warnings).

## ⚠️ WARNINGS — read before mainnet value flows

1. **Contracts are unaudited.** Two adversarial reviews caught real bugs
   pre-merge (push-only payout bricking → pull-payment fallback; silent
   uint256→uint96 truncation → `PriceTooLarge` revert). Treat the code as
   beta-grade: cap initial mainnet exposure and run a bug-bounty window.
2. **Single-key adjudicator is the known mainnet-value blocker.** The
   adjudicator alone can slash 100% (ghosting) / 50% (quality) of worker
   stake via `resolveQualityDispute`. Until a multisig/threshold adjudicator
   (or the deferred poster fast-path + optimistic batch) is decided AND
   deployed, do not route meaningful value through these contracts.
3. **Single-key owner on the auction core** (`setEscrowManager`,
   `setExecutionParams`) — same centralization caveat; plan ownership
   transfer to the same multisig as (2).
4. **Deployer key hygiene:** the deployer key becomes `owner`. Use a fresh
   key, fund it minimally, and rotate/retire it after ownership transfer.
5. **Stake is exact-value and slashable** — workers must understand
   `depositStake` requires the precise stake and ghosting burns it entirely.
