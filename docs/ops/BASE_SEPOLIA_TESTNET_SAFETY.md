# Base Sepolia Testnet Safety and Gas Preflight

This runbook covers **read-only readiness checks** for Base Sepolia (chain ID `84532`). It does not authorize deployments, live transactions, faucet requests, or automated activity generation.

## Network configuration

Base documents `https://sepolia.base.org` as the Base Sepolia RPC endpoint and `84532` as its chain ID ([Base network documentation](https://docs.base.org/get-started/connect-to-base)). The optional public fallback in the preflight tool is `https://base-sepolia-rpc.publicnode.com` ([PublicNode endpoint page](https://base-sepolia-rpc.publicnode.com/)). Public endpoints are rate-limited/availability-dependent; use a provider account where appropriate and do not put credential-bearing RPC URLs in logs or screenshots.

The root `foundry.toml` includes a `base_sepolia` alias that reads only `BASE_SEPOLIA_RPC_URL`. For the official default:

```bash
export BASE_SEPOLIA_RPC_URL=https://sepolia.base.org
```

Use Foundry's `--rpc-url base_sepolia` or `--rpc-url "$BASE_SEPOLIA_RPC_URL"` only for explicitly authorized testnet operations. Before broadcast, independently verify `eth_chainId == 84532`, target contract addresses, and the test-only signer identity. This alias does not itself make any command safe to broadcast.

## Read-only gas preflight

Set the addresses of **dedicated test wallets** (not deployment, treasury, or mainnet signer addresses):

```bash
export BASE_SEPOLIA_TEST_ADDRESSES=0xYourTestWallet1,0xYourTestWallet2
python scripts/check_base_sepolia_gas.py
```

Or pass them explicitly:

```bash
python scripts/check_base_sepolia_gas.py \
  --address 0xYourTestWallet1 \
  --address 0xYourTestWallet2 \
  --minimum-eth 0.05
```

The command verifies the network, reads balances (up to 32 addresses per run), and exits non-zero if any address is below the threshold. It does not load private keys, sign, broadcast, call a faucet, or automatically fund a wallet. A low balance is an operator stop condition: acquire test ETH manually from a reputable faucet under that faucet's terms, then rerun the check. The default is `0.05 ETH`; override only for a documented test budget.

The auction relayer has a matching guard: when it is connected to Base Sepolia it refuses to sign/broadcast if the relayer balance is below `AUCTION_SEPOLIA_MIN_GAS_ETH` (default `0.05`). Set `AUCTION_REQUIRE_BASE_SEPOLIA=true` in a testnet-only environment to additionally refuse every non-Sepolia chain. These are pre-send checks, not a wallet funding mechanism. Keep `AUCTION_ONCHAIN_ANCHOR` and `AUCTION_ONCHAIN_FUND` disabled unless a separately reviewed testnet deployment and exact test transaction have been approved.

## Synthetic auction testing

Prefer the existing in-memory EthereumTester auction suites and gas regression tests for deterministic lifecycle and gas measurements. They avoid live RPC, faucet, and transaction effects. Where a live Base Sepolia integration test is necessary, use dedicated test identities, label generated transactions and accounts as test artifacts in documentation/monitoring, bound the request and transaction rate, and test only the intended protocol mechanics. **Do not represent synthetic activity as organic, use address rotation to evade faucet controls, or automate wash-like volume.** Testnet logs should be truthful about their synthetic origin.

## Not included by design

- No multi-faucet harvesting, address rotation, captcha/rate-limit bypass, or automated faucet requests.
- No primary deployment/treasury keys in test scripts; no private-key input or management in the preflight tool.
- No changes to auction timing, storage layout, calldata format, settlement, or deployed contracts.
- No automated wallet refill, transaction broadcast, or high-frequency market-activity loop.

These boundaries preserve the ability to run legitimate tests without increasing signer exposure or creating misleading activity. Smart-contract gas changes should be proposed separately with reproducible before/after measurements and the full commit/reveal, selection, and escrow regression suite.
