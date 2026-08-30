# Contract-Net Task Market

Additive market path beside the existing first-price `BiddingEngine.run_auction`.
Nothing in this package mutates the first-price scoring weights, TaskMarket
award loop, or `/api/marketplace` routes.

## Why

Broadcasting every task to the full swarm and asking each agent to draft an LLM
bid burns tokens before work starts. Incumbents then win every first-price
round, so juniors never build merit.

## Mechanism

1. **Phase 1 — cosine invite.** Task requirements and agent skills are hashed
   into 64-d Keccak bag-of-tokens vectors. Only the top `invite_k` (3–5)
   agents are invited. Tokens saved = `(n − k) × 800`.
2. **Phase 2 — sealed Vickrey reverse auction.** Invited agents submit one
   EIP-712 signed bid. Lowest valid price wins and is paid the second-lowest
   valid price. No counter-bids. Dominant strategy is to bid true minimum
   margin.
3. **ε-greedy junior reservation.** `epsilon ∈ [0.10, 0.15]` (default 0.12)
   of auctions invite **only** juniors (`is_junior` or `tasks_completed < 3`).
   Those invites carry a 2× eval-token subsidy. If the junior pool is empty
   the auction falls back to the full ranking so the market never stalls.

## EIP-712 domain (Base)

| Field | Value |
| --- | --- |
| name | `SINCOR Contract-Net` |
| version | `1` |
| chainId | `8453` |
| verifyingContract | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` (treasury) |

Primary type `Bid`: `auctionId bytes32`, `taskId string`, `agent address`,
`price uint256` (micro-AXM), `estimatedTokens uint256`, `nonce uint256`,
`deadline uint256`.

Signatures: secp256k1 via `eth_account` when a key is present, otherwise
HMAC-SHA256 over the typed-data digest (demo roster).

## API

Prefix `/api/contract-net`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Domain + k + ε |
| GET | `/roster` | Demo swarm |
| GET | `/tasks` | Demo task board |
| GET | `/stats` | Aggregate junior share / tokens saved |
| GET | `/history` | Recent awards |
| POST | `/auctions` | One sealed round |
| POST | `/simulate` | Batch rounds |

## Integration

- `marketplace.contract_net.ContractNetEngine` is the library.
- `BiddingEngine.run_vickrey_auction` is a thin additive wrapper; `run_auction`
  is unchanged.
- Flask registers `contract_net_bp` inside `create_app()` in a try/except so a
  missing module cannot take down the rest of the app.
