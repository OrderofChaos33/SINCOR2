# Chainlink feed pins (Base mainnet)

Companion to `src/sincor2/defi/price_oracle.py` (`BASE_MAINNET_FEEDS`,
`make_eth_call_reader`). Records exactly which aggregator proxies are
pinned, where the addresses were verified, and what is deliberately NOT
pinned.

Verified: 2026-09-29 (UTC). Chain: Base mainnet (chain id 8453).

## Pinned feeds

| Asset | Feed proxy (checksummed) | Verified against |
|---|---|---|
| ETH/USD | `0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70` | Chainlink's official Base feed registry (https://data.chain.link/feeds/base/base/eth-usd, cited by st0x.liquidity ADR-0020); SINCOR2's own `onchain/DEPLOYING.md` (`CHAINLINK_ETH_USD`, fork-verified in `test/SincLoopFork.t.sol`); Exactly Protocol docs (`guides/price-feeds.md`); Moonwell `moonwell-contracts-v2` constants; Superform v2-core; evmx; basedex; claimrush; wpank/bardo; morscan.io; unboxed-loyalty-spark — 11+ independent corroborations total |
| USDC/USD | `0x7e860098F58bBFC8648a4311b374B1D669a2bc6B` | Moonwell `moonwell-contracts-v2` constants; wpank/bardo mirage config; Superform v2-core; Exactly Protocol docs — 4 independent corroborations |

Direct page load of `docs.chain.link/data-feeds/price-feeds/addresses?network=base`
failed once during verification (fetch timeout; not retried), so the two
rows above rest on the independent sources listed. Both addresses must be
re-checked against the official docs page before any production use.

## Deliberately NOT pinned

- **BTC/USD (Base): three conflicting addresses across sources** —
  `0x64c911996D3c6aC71f9b455B1E8E7266BcfBF15c` (basedex),
  `0x64c911996D3c6aC71f9b455B1E8E7266BcbD848F` (Moonwell),
  `0x07DA0E54543a844a80ABE69c8A12F22B3aA59f9D` (Superform — but this one
  matches Exactly Protocol's **cbBTC/USD** feed, so it is almost certainly
  mislabeled). Guessing between them would be a fiction; the asset stays
  unset until one address is confirmed against docs.chain.link. The oracle
  is fail-closed: an unpinned asset yields `None` from the reader and
  `NoPriceAvailable` from the oracle, never a guess.

## How to add a feed

1. Verify the proxy address on docs.chain.link's address page (not from a
   third-party repo, not from memory).
2. Add it to `BASE_MAINNET_FEEDS` with the source + date recorded here.
3. The reader fetches `decimals()` on-chain at construction; no hardcoded
   decimals anywhere.

## Honest limits of the live read seam

- Read-only: `eth_call` at `"latest"` only. No transactions, no signing,
  no keys — the seam cannot move funds by construction.
- **L2 sequencer uptime is NOT checked.** Chainlink's own L2 guidance says
  to consult the Base sequencer-uptime feed before trusting answers; that
  feed is not pinned here yet, so a sequenced-down window could serve a
  stale-but-valid round. The oracle's `max_age_s` staleness guard catches
  the symptom (round stops updating), not the cause.
- No block pinning and no caching: each `read_round` call is one RPC
  round-trip. Production use needs caching + rate-limit discipline against
  the RPC provider.
- No monitoring: the reader exposes `stats()` (calls/errors/last_error)
  but nothing alerts on it yet. Monitoring on the audit log + reader
  stats is still missing.
- One live asset is not a market: `min_sources=2` default means a single
  Chainlink adapter alone cannot publish (fail-closed by design) — a real
  deployment needs the second source (e.g. DexTwapAdapter) wired and live
  before any product can read prices.
