# SINC Token Integration

This document describes how SINC (`0x9C8cd8d3961F445D653713dE65C6578bE11668e7` on Base)
is integrated as the exclusive access and utility token across the SINCOR2 platform.

---

## Token & Contract Addresses

| Contract | Address | Network |
|---|---|---|
| SINC v3 | `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` | Base |
| SinCurve (bonding curve) | `0x75dE341a2BC81806198364F125d4Cde36527619C` | Base |
| SINCPlatformAccess | Set via `SINC_PLATFORM_ACCESS_ADDRESS` env var | Base |
| Treasury | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` | Base |

SINC uses `decimals = 0` — all amounts are whole integers (no wei conversion needed).

---

## Environment Variables

| Variable | Description | Required |
|---|---|---|
| `SINC_CONTRACT_ADDRESS` | SINC ERC-20 token address | No (defaults to v3 address) |
| `SINC_PLATFORM_ACCESS_ADDRESS` | Deployed `SINCPlatformAccess.sol` address | Required for credit/staking features |
| `BASE_RPC_URL` | JSON-RPC endpoint for Base mainnet | Yes for on-chain reads |
| `PLATFORM_SIGNER_KEY` | Private key for `spendCredits`/`releaseEscrow` backend signing | Required for metered billing |

---

## Access Tier Table

| Feature | SINC Requirement | Model |
|---|---|---|
| Basic dashboard access | 0 SINC | Free |
| Agent Cards / Marketplace browsing | 0 SINC | Free |
| Standard agent call (1 call) | 1 SINC credit | Pay-per-use |
| Advanced agent features | 500 SINC held | Balance gate |
| Agent marketplace listing | 250 SINC staked + 50 SINC listing fee | Stake + fee |
| Priority routing | 1,000+ SINC staked | Stake tier |
| Agent swarm (multi-agent) | 10 SINC/hour (credits) | Metered credits |
| A2A external task | 1 SINC/call | Pay-per-call |
| Enterprise/custom agents | 5,000 SINC staked | Stake tier |

**Staking discount:** Users staking ≥ 1,000 SINC receive a 20% discount on credit usage.

---

## Architecture Overview

```
User Wallet (Base) ──► SINC Balance Check ──► Platform Access Grant
        │                                              │
  sinc_required()                         SINC balance widget
  Flask decorator                         (dashboard header)
        │                                              │
  Backend API Endpoints ◄──────── SINCAccessManager
  /api/sinc/balance                        │
  /api/sinc/quote                    Base RPC (read-only)
  /api/sinc/credits/purchase               │
  /api/sinc/credits/spend         SINCPlatformAccess.sol
        │                         (escrow / staking / credits)
  A2A & Marketplace
  settlement.py ──► SINC-only quotes + 5% fee → Treasury
  registry.py  ──► SINC pricing metadata on AgentCards
        │
  Treasury: 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac
```

All on-chain actions (credit purchase, staking, escrow) are signed client-side via
MetaMask/WalletConnect. The backend verifies state via read-only RPC calls and never
holds private keys for on-chain user funds (only the platform signer key for metered billing).

---

## Backend: `@sinc_required` Decorator

Located in `src/sincor2/sinc_access.py`.

### Usage

```python
from sincor2.sinc_access import sinc_required

# Require 500 SINC held
@bp.route("/advanced-feature")
@sinc_required(min_balance=500)
def advanced_feature():
    ...

# Require 250 SINC staked
@bp.route("/marketplace/register", methods=["POST"])
@sinc_required(min_staked=250)
def register_agent():
    ...

# Require 10 SINC credits
@bp.route("/run-agent", methods=["POST"])
@sinc_required(min_credits=10)
def run_agent():
    ...
```

### How It Works

1. Reads `X-Wallet-Address` header from the request.
2. If no header: returns `402 Payment Required` immediately.
3. Calls `SINCAccessManager` (registered in `app.extensions["sinc_access"]`):
   - `verify_minimum(wallet, min_balance)` — on-chain `balanceOf` via Base RPC.
   - `verify_staked(wallet, min_staked)` — reads `staked[wallet]` from `SINCPlatformAccess`.
   - For `use_credits`: reads `credits[wallet]` from `SINCPlatformAccess`.
4. On failure: returns JSON `{"error": "insufficient_sinc", "required": N, "current": M}` with status 402.
5. Balances cached for 15 seconds per wallet to reduce RPC calls.

### 402 Response Schema

```json
{
  "error": "insufficient_sinc",
  "required": 500,
  "current": 120,
  "wallet": "0xabc..."
}
```

---

## Backend: `SINCAccessManager`

```python
from sincor2.sinc_access import SINCAccessManager

mgr = SINCAccessManager(
    rpc_url="https://mainnet.base.org",
)

balance = mgr.get_balance("0xUserWallet")    # on-chain balanceOf
credits = mgr.get_credits("0xUserWallet")   # SINCPlatformAccess credits[]
staked  = mgr.get_staked("0xUserWallet")    # SINCPlatformAccess staked[]
mgr.invalidate_cache("0xUserWallet")        # force fresh read
```

---

## Backend: `SINCMeter` (Usage Logging)

```python
from sincor2.sinc_access import SINCMeter

meter = SINCMeter(log_path="sinc_usage_log.json")
meter.record(
    wallet="0xUserWallet",
    action_type="agent_call",
    sinc_amount=1,
    task_id="task-uuid",
)
```

Records are appended to `sinc_usage_log.json` (or a path set by `SINC_USAGE_LOG_PATH` env var).
Each record includes: `wallet`, `action_type`, `sinc_amount`, `task_id`, `timestamp` (ISO 8601).

---

## API Endpoints (`/api/sinc/`)

All endpoints are registered in `src/sincor2/blueprints/sinc.py`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sinc/balance` | On-chain balance + credits + staked (`?wallet=0x...`) |
| `POST` | `/api/sinc/quote` | SINC cost estimate for a given action |
| `POST` | `/api/sinc/credits/purchase` | Returns calldata for `purchaseCredits` |
| `POST` | `/api/sinc/credits/spend` | Internal: deducts credits (JWT-authenticated) |
| `GET` | `/api/sinc/history` | Paginated SINC transaction history (`?wallet=0x...`) |
| `POST` | `/api/sinc/stake` | Returns calldata for `stake()` |
| `POST` | `/api/sinc/unstake` | Returns calldata for `requestUnstake()` |
| `GET` | `/api/sinc/tiers` | Returns access tier definitions |

---

## Credit Purchase Flow

```
1. User clicks "Purchase Credits" in dashboard
2. Frontend calls POST /api/sinc/credits/purchase with {wallet, amount}
3. Backend returns {calldata, contract, value} for SINCPlatformAccess.purchaseCredits(amount)
4. Frontend presents MetaMask approval:
   a. First: ERC-20 approve(SINCPlatformAccess, amount)
   b. Then:  purchaseCredits(amount)
5. On tx confirmation, credits[wallet] increases on-chain
6. Dashboard widget refreshes via GET /api/sinc/balance
```

**Minimum purchase:** 10 SINC.
**Credit ratio:** 1 SINC = 1 credit. Credits never expire.
**Platform fee:** 5% of credit spend is routed to treasury on each `spendCredits` call.

---

## Staking

```
1. User calls POST /api/sinc/stake with {wallet, amount}
2. Backend returns contract calldata for SINCPlatformAccess.stake(amount)
3. User signs via MetaMask (ERC-20 approve + stake)
4. staked[wallet] increases; user earns priority routing boost
```

**Unstake cooldown:** 7 days from `requestUnstake()` call.
**Staking discount:** ≥ 1,000 SINC staked → 20% discount on all credit usage.

---

## Smart Contract: `SINCPlatformAccess.sol`

Source file: `SINCPlatformAccess.sol` (root of repository).

Key functions:

```solidity
function purchaseCredits(uint256 amount) external;   // user buys credits
function spendCredits(address user, uint256 amount, bytes32 taskId) external; // backend billing
function stake(uint256 amount) external;             // lock SINC for tier access
function requestUnstake(uint256 amount) external;    // 7-day cooldown
function claimUnstake() external;                    // after cooldown
function escrowTask(bytes32 taskId, address payee, uint256 amount) external;
function releaseEscrow(bytes32 taskId) external;     // backend signer releases
function claimFees() external;                       // sweep fees to treasury
```

Security properties:
- `ReentrancyGuard` on all state-changing functions.
- CEI (Checks-Effects-Interactions) pattern throughout.
- `spendCredits` and `releaseEscrow` require a valid ECDSA signature from the platform signer.
- Treasury address is `immutable` (set at deploy, cannot be changed).
- No owner or upgrade mechanism — deploy new contract if changes needed.

---

## On-Chain Events (for indexers/observability)

```solidity
event CreditsPurchased(address indexed user, uint256 amount);
event CreditsSpent(address indexed user, uint256 amount, bytes32 taskId);
event Staked(address indexed user, uint256 amount);
event Unstaked(address indexed user, uint256 amount);
event FeePaid(address indexed user, uint256 fee, address treasury);
event EscrowReleased(bytes32 indexed taskId, address payer, address payee, uint256 amount);
```

These events can be indexed by any EVM event indexer (e.g., The Graph, Alchemy webhooks)
to build off-chain analytics, leaderboards, and activity feeds.

---

## A2A & Marketplace

**Settlement:** `marketplace/settlement.py` now uses SINC as the primary token.
Every `create_quote()` returns a `sinc_amount` field. `confirm_payment()` automatically
routes 5% of the SINC amount to the treasury and records `platform_fee` and `payee_amount`
on the `SettlementRecord`.

**Agent Cards:** `AgentCardRecord` now includes:
- `sinc_price_per_call` (default 1)
- `sinc_price_per_minute` (default 0)
- `sinc_stake_required` (default 250)

Populate these in your agent card JSON under a `sincPricing` block:

```json
{
  "name": "My Agent",
  "sincPricing": {
    "pricePerCall": 2,
    "pricePerMinute": 0,
    "stakeRequired": 250
  }
}
```

**Marketplace registration** requires `min_staked=250` SINC (verified on-chain). The
`X-Wallet-Address` header must be sent with registration requests.

---

## Migration Notes

See [ARCHITECTURE.md](./ARCHITECTURE.md) or the inline migration plan in the original
architecture document for the phased rollout plan (Phases 1–5). Key points:

1. **No wallet, no advanced features** — basic browsing remains free.
2. **Non-breaking parallel deploy** — SINC features gate new functionality first.
3. **Legacy users** receive SINC credit airdrops equivalent to remaining subscription value.
4. **`A2A_PRIMARY_TOKEN` env var** — set to `AXIOM` for backward-compat legacy mode.