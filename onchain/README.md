# SINC + AXIOM — Onchain Contracts

Foundry project for all SINCOR ecosystem smart contracts, deployed on **Base** (chainId 8453).

---

## Token overview

| Token | Symbol | Contract (Base mainnet) | Supply | Decimals | Role |
|-------|--------|------------------------|--------|----------|------|
| SINC  | SINC   | `0xe1D836087F6573b665d25CE088793E916D7892f8` | 1 B   | 8  | Platform utility token; burned via agent-billing loop |
| AXIOM | AXM    | `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a` | 1 B   | 18 | Autonomous intelligence token; A2A inter-agent settlement |

Both tokens: fixed supply, no mint, no owner, no tax, no proxy. Verified on Basescan.

**Official price floor:** $0.15 USD per SINC ($150M FDV / 1B tokens).

**Treasury (owner for Morpho setup / oracle):** `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`

---

## Contract directory

```
src/
├── SincBondingCurve.sol   # Constant-product bonding curve for SINC Phase 1
├── SincGenesisNFT.sol     # Soulbound ERC-721 minted to Phase 1 buyers
├── SincLimitOrderHook.sol # Uniswap V4 hook: limit orders + anti-sandwich fee
├── Axiom.sol              # AXIOM ERC-20 — A2A settlement token
├── SincPriceOracle.sol    # Curve + ETH/USD style price helper
├── interfaces/
│   └── AggregatorV3Interface.sol  # Chainlink AggregatorV3
└── morpho/
    ├── SincChainlinkOracle.sol  # Morpho IOracle — hybrid manual+feed, hard $0.15 floor
    ├── SincMorphoSetup.sol      # Morpho Blue market creation helper (AdaptiveCurveIRM)
    └── SincStaking.sol          # Staking with pause + emergency withdraw

script/
├── 00_DeployMockSinc.s.sol … 06_DeployAxiom.s.sol
└── Deploy.s.sol, DeployMoebius.s.sol, …
```

---

## Morpho Blue — SINC/USDC oracle (July 2026)

`SincChainlinkOracle` implements Morpho’s `IOracle.price()` at **1e36 scale** for SINC (8 decimals) / USDC (6 decimals).

| Constant | Value | Meaning |
|----------|-------|---------|
| `PRICE_FLOOR_8DEC` | `15_000_000` | $0.15 with 8-dec Chainlink-style answer |
| `SCALE_FACTOR` | `1e26` | Converts 8-dec feed → Morpho 1e36 for 8/6 pair |
| Floor Morpho-scaled | `0.15e34` | Minimum `price()` return |

Behavior:

- Starts in **manual mode** at exact floor (treasury owner).
- `setFeed(address)` switches to Chainlink-style `AggregatorV3Interface`.
- Any feed answer below $0.15 is **clamped to floor**.
- Staleness, invalid round, and zero-price reverts are enforced.

`SincMorphoSetup` targets Morpho Blue on Base (`0xBBBB…`) with AdaptiveCurveIRM and a `createSincUsdcMarket` helper.

`SincStaking` supports reward accounting and `emergencyWithdraw` when paused.

---

## Setup

```bash
forge install
forge build
forge test
```

## Environment

Copy `onchain/.env.example` to `onchain/.env` and fill in:

| Variable | Description |
|----------|-------------|
| `BASE_RPC_URL` | Base mainnet RPC |
| `BASE_SEPOLIA_RPC_URL` | Base Sepolia RPC |
| `BASESCAN_API_KEY` | Contract verification |
| `DEPLOYER_PRIVATE_KEY` | Hot wallet for scripts (never the treasury key) |
| `TREASURY_ADDRESS` | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` |

## Test

```bash
forge test -vvv
forge test --match-contract SincBondingCurve -vvv
```

## Deploy notes

Deploy scripts live under `script/`. Run in order against Sepolia first, then Base. Morpho oracle / setup contracts should be deployed with **treasury as `initialOwner`**.

---

## Supply allocation (SINC — 100 M)

| Bucket | Amount | Notes |
|--------|--------|-------|
| Bonding curve (Phase 1 + LP seed) | 65 M | Consumed by buyers; remainder paired into V4 LP and burned |
| Concentrated $1.50 ceiling LP | 5 M | Single-tick V4 position |
| Sell-side limit-order ladder | 20 M | Hook ladder |
| Sablier 24-month linear vest | 10 M | Non-cancellable stream |

## Supply allocation (AXIOM — 1 B)

| Bucket | Amount | Notes |
|--------|--------|-------|
| Ecosystem / A2A treasury | 80 % | Agent-to-agent payment pool |
| Team / development | 10 % | 24-month vest recommended |
| Liquidity (Uniswap V4) | 10 % | Seeded at launch; LP burned |

