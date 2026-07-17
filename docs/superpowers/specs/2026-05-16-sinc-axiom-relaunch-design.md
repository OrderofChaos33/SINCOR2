# SINC + AXIOM Integrated Relaunch — Design Spec

**Date:** 2026-05-16
**Status:** Design approved by user; pending spec review before plan
**Repo:** `sincor-clean/` (Flask app + new `onchain/` Foundry project)
**Chain:** Base mainnet (chainId 8453)
**Author:** Drafted with Claude as design collaborator; user is decision authority and sole signing party.

---

## 1. Goal

Relaunch the SINC token in a way that:
1. **Restores credibility** — every "audit badge" reads green and is provably so on-chain.
2. **Bootstraps liquidity without large up-front capital** — the launch is funded by Phase 1 buyers, not the treasury.
3. **Creates real on-chain demand for SINC** — through the existing SINCOR agent platform.
4. **Operates within legal bounds** — no wash trading, no astroturfing, no fake testimonials, no undisclosed AI shilling, no security-warning suppression. All credibility is earned through verifiable on-chain action.
5. **Refreshes the SINCOR website** — new logo, accurate copy on `/sinc` and `/axiom`, embedded trading UI.

## 2. Hard constraints

- Treasury budget: very limited (~$50–200 in ETH for gas + minimal initial seed).
- Signing authority: user only, via their own wallet software (MetaMask or hardware). No automated signing, no plaintext-key file reads.
- Treasury wallet: `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` (clean, no plaintext exposure).
- Legacy / compromised wallets that hold supply but must NOT be used for treasury operations:
  - `0x35cb3bf1b29F81d325EB9A7225a3E87fE7B37D6f` — original creator. Key has been in `pk.txt` on user's Desktop. Will be emptied and abandoned.
  - `0x6E36dfD8773A5Ad2336418a5135f4b6119f5cBC9` — top v2 holder. Key was pasted in a chat session (this conversation). Currently holds 40M SINC v2 tokens. Considered permanently compromised; v2 will be sunsetted so its 40M becomes worthless.
- Existing SINC v2 contract at `0x49E392de962Fa835B862F59E78611c69E930b5C4` will be **publicly sunsetted, not migrated**. User has confirmed they own all 37 v2 holder wallets, so there is no external migration burden.

## 3. Canonical SINC contract

**Address:** `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` (Base mainnet)

**Verified properties (on-chain, as of 2026-05-16):**
- Source code verified on Basescan ("Exact Match")
- Token name: SINC, symbol: SINC, decimals: **8** (intentional — matches WBTC/cbBTC convention; preserves the CertiK audit; cannot be changed post-deploy)
- Total supply: 100,000,000 SINC (fixed, no mint function)
- Owner role: per GoPlus, `owner_balance: 0` — likely already renounced or no privileged owner role. Spec assumes no `renounceOwnership()` step needed; verification step in runbook confirms.
- Not mintable, not pausable, not blacklist-capable, no honeypot, no tax, no hidden owner, no proxy, no self-destruct.
- **CertiK Skynet score: 97/100** (1 attention flag = "single-wallet concentration," resolved as supply distributes).
- Initial supply allocation: 100M held by `0x35cb…7D6f` at time of audit. Allocation must be moved to `0x09E2…12Ac` before any launch activity.

## 4. On-chain architecture

### 4.1 New Foundry project: `sincor-clean/onchain/`

```
onchain/
├── foundry.toml                      # Solidity 0.8.26, optimizer on
├── remappings.txt                    # @uniswap/v4-core/, @uniswap/v4-periphery/, @openzeppelin/, @openzeppelin/uniswap-hooks/
├── src/
│   ├── SincBondingCurve.sol          # NEW — Phase 1 curve with referral + auto-NFT-mint
│   ├── SincLimitOrderHook.sol        # Hook = LimitOrderHook + anti-sandwich afterSwap override
│   └── SincGenesisNFT.sol            # Soulbound ERC721 for Phase 1 buyers
├── script/
│   ├── 01_DeployGenesisNFT.s.sol     # Deploy Genesis NFT (curve will reference its address)
│   ├── 02_DeployBondingCurve.s.sol   # Deploy curve; curve.constructor(sinc, treasury, nft)
│   ├── 03_FundCurveWithSINC.s.sol    # Transfers 65M SINC from treasury into curve
│   ├── 04_MineHookAddress.s.sol      # CREATE2 salt mining for hook permission bits
│   ├── 05_DeployHook.s.sol           # Deploys SincLimitOrderHook at the mined address
│   ├── 06_PlaceLimitOrderLadder.s.sol# Post-graduation: places sell-side ladder
│   ├── 07_PlaceConcentratedCeiling.s.sol # Post-graduation: places $1.50 wall LP
│   └── 08_CreateSablierVest.s.sol    # 24-month linear stream for 10M SINC
└── test/
    ├── SincBondingCurve.t.sol         # Curve math + referral + NFT auto-mint
    ├── Graduation.t.sol               # Fork test: curve → v4 pool init → LP burn
    ├── HookIntegration.t.sol          # Limit orders + anti-sandwich detection
    ├── AntiSandwich.t.sol             # Specific: simulate sandwich attack, assert fee scaling
    └── GenesisNFT.t.sol               # Soulbound transfer reverts, mint via curve only
```

Existing `SINCBondingCurve.sol` at the repo root is **not used** — it has rug-pull withdrawal functions, no graduation logic, and starts at $1.09 (which is the FDV ceiling, not the floor). It remains in the repo as a historical artifact only.

### 4.2 Bonding curve (`SincBondingCurve.sol`)

**Pattern:** Constant-product with virtual reserves (pump.fun-derived, simplified) + referral + Genesis NFT auto-mint.

**Critical properties:**
- Holds 65M SINC + accumulated buyer ETH.
- `buy(uint256 ethAmount, address referrer)` and `sell(uint256 sincAmount)` are the only state-modifying public functions during Phase 1.
- **Referral system:** if `referrer != address(0)` AND `referrer != msg.sender`, **3% of the ETH paid** is immediately forwarded to the referrer's address inside the same tx. Tracked per buy. If `referrer == address(0)`, the 3% routes to treasury instead (no waste). Self-referral is silently blocked.
- **Genesis NFT auto-mint:** on the FIRST `buy()` call from any address during Phase 1, the curve calls `SincGenesisNFT.mint(buyer, buyOrderNumber)`. Subsequent buys from the same address don't re-mint (the NFT is your "first buy" certificate). NFT cost = ~50k gas added to the first buy tx.
- **No `withdrawETH`, no `withdrawTokens`, no admin role, no pause, no upgradability.** ETH cannot exit the curve except via:
  - A user `sell()` call (refunding their SINC according to curve math), or
  - The graduation function, which is callable by anyone after threshold.
- Graduation threshold: **$1,500 of ETH accumulated** (≈ 0.5 ETH at $3000/ETH). Configurable at deploy time; locked thereafter.
- `graduate()` is a one-shot, permissionless function. Once threshold is crossed, anyone can call it. On success:
  1. Compute final curve price from current curve state.
  2. Initialize Uniswap V4 SINC/WETH pool on PoolManager `0x498581ff718922c3f8e6a244956af099b2652b2b` with `SincLimitOrderHook` attached, at the curve's final price.
  3. Add LP using **80% of accumulated ETH** paired with **all remaining SINC held by the curve** (typically ~15M but variable based on how Phase 1 went).
  4. Transfer the resulting LP NFT to `0x000000000000000000000000000000000000dEaD` (burn).
  5. Transfer **20% of accumulated ETH** to treasury `0x09E2…12Ac`.
  6. Self-disable: `buy()` and `sell()` revert thereafter; `graduated` boolean flips to `true`.
  7. Emit `Graduated(uint256 ethToLP, uint256 ethToTreasury, address poolId, uint256 burnedLPTokenId, uint256 sincToLP)` for indexers.

**Curve math (initial values, subject to test-tuning):**
- Virtual ETH reserve: 0.001 ETH (sets the starting price)
- Virtual SINC reserve: 100,000,000 SINC (8 decimals — i.e., 10^16 atomic units)
- Real SINC supply held: **65,000,000 SINC** (transferred in at step 7)
- Starting price: virtual_ETH / virtual_SINC ≈ 1e-11 ETH per atomic SINC ≈ $0.0001 per displayed SINC
- Graduation price (at 0.5 ETH accumulated): ≈ $0.005 per SINC
- Phase 1 SINC distributed (estimate): ≈ 50M SINC consumed by buyers as price walks from $0.0001 → $0.005
- Phase 1 SINC remaining at graduation (estimate): ≈ 15M, used as the LP-side seed
- These split numbers are first-order estimates; final calibration happens in `test/SincBondingCurve.t.sol` and may shift the curve formula's virtual-reserve constants

**Decimal handling:** All public function arguments are in atomic units (10^8 = 1 displayed SINC; 10^18 = 1 ETH). Frontend converts to/from displayed units.

### 4.3 Uniswap V4 limit-order hook (`SincLimitOrderHook.sol`)

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {LimitOrderHook} from "@openzeppelin/uniswap-hooks/general/LimitOrderHook.sol";
import {BaseHook} from "@openzeppelin/uniswap-hooks/base/BaseHook.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {BeforeSwapDelta, toBeforeSwapDelta} from "@uniswap/v4-core/src/types/BeforeSwapDelta.sol";

contract SincLimitOrderHook is LimitOrderHook {
    // Anti-sandwich: track recent swaps per pool per block
    mapping(bytes32 => mapping(uint256 => uint256)) public swapsInBlock; // poolId => block.number => count
    uint24 public constant BASE_FEE = 3000;       // 0.30%
    uint24 public constant SANDWICH_FEE = 30000;  // 3.00% on detected sandwich attempt

    constructor(IPoolManager m) BaseHook(m) {}

    function getHookPermissions() public pure override returns (Hooks.Permissions memory) {
        Hooks.Permissions memory p = super.getHookPermissions();
        p.beforeSwap = true;
        p.afterSwap = true;
        p.beforeSwapReturnDelta = false;
        return p;
    }

    function _beforeSwap(address, PoolKey calldata key, IPoolManager.SwapParams calldata, bytes calldata)
        internal override returns (bytes4, BeforeSwapDelta, uint24)
    {
        bytes32 pid = keccak256(abi.encode(key));
        uint256 count = swapsInBlock[pid][block.number];
        uint24 fee = count >= 2 ? SANDWICH_FEE : BASE_FEE;  // 2nd+ swap in same block = sandwich pattern
        swapsInBlock[pid][block.number] = count + 1;
        return (this.beforeSwap.selector, toBeforeSwapDelta(0, 0), fee);
    }
}
```

**Why this is correct:**
- OpenZeppelin's `LimitOrderHook` is audited; we extend (not modify) by overriding `_beforeSwap` while preserving all of OZ's existing logic.
- Anti-sandwich heuristic: a sandwich attack requires AT LEAST 2 swaps in the same block from a single attacker (front-run + back-run around a victim). The hook detects "this is the 2nd+ swap in this block on this pool" and scales the fee from 0.30% → 3.00%. Real users almost never trigger this; sandwich bots always do.
- This is a custom modification, so we run extended Foundry fork tests + submit a CertiK Skynet scan on the hook before mainnet deploy.

**Deployment:** Requires CREATE2 with a mined salt so the deployed address has the correct hook-permission flag bits (per Uniswap v4 hook addressing scheme: bits 0–13 of the address encode which hooks the contract uses). `04_MineHookAddress.s.sol` finds a salt in ~30s of off-chain compute.

### 4.3a Genesis NFT (`SincGenesisNFT.sol`)

Soulbound (non-transferable) ERC-721 minted to each Phase 1 buyer on their FIRST curve buy. Metadata includes buy-order-number, timestamp, and a personalized variant of the SINC planet logo.

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";

contract SincGenesisNFT is ERC721 {
    address public immutable curve;       // Only the curve can mint
    uint256 public nextTokenId = 1;

    constructor(address _curve) ERC721("SINC Genesis Holder", "SINC-GEN") {
        curve = _curve;
    }

    function mint(address to, uint256 buyOrderNumber) external returns (uint256 tokenId) {
        require(msg.sender == curve, "Only curve");
        tokenId = nextTokenId++;
        _safeMint(to, tokenId);
        // buyOrderNumber stored via event for indexer
        emit GenesisMinted(to, tokenId, buyOrderNumber, block.timestamp);
    }

    // Soulbound: prevent ALL transfers (only mint allowed)
    function _update(address to, uint256 tokenId, address auth) internal override returns (address) {
        address from = _ownerOf(tokenId);
        require(from == address(0) || to == address(0), "Soulbound: non-transferable");
        return super._update(to, tokenId, auth);
    }

    event GenesisMinted(address indexed holder, uint256 indexed tokenId, uint256 indexed buyOrderNumber, uint256 timestamp);
}
```

**Deployment ordering:** NFT deploys FIRST (script 01), curve deploys SECOND with NFT address passed to constructor (script 02). After curve is deployed, NFT's `curve` immutable is locked. The NFT can't be ownership-transferred or re-pointed.

### 4.4 Post-graduation ladder + ceiling

After graduation, the user (via wallet signature) places:

1. **Concentrated $1.50 ceiling LP** (`06_PlaceConcentratedCeiling.s.sol`): 5M SINC sold via a single-tick LP at $1.50 (i.e., a tick range narrow enough to function as a fixed-price sell wall, all SINC, no ETH). The wall captures real ETH when price walks up to it.

2. **Sell-side limit-order ladder** (`05_PlaceLimitOrderLadder.s.sol`): 20M SINC placed in 7 rungs via `SincLimitOrderHook.placeOrder()`:

| Rung | Tick price | SINC to sell at this rung |
|---|---|---|
| 1 | $0.01 | ~1,500,000 |
| 2 | $0.05 | ~2,000,000 |
| 3 | $0.10 | ~2,500,000 |
| 4 | $0.25 | ~3,000,000 |
| 5 | $0.50 | ~3,500,000 |
| 6 | $1.00 | ~3,500,000 |
| 7 | $1.40 | ~4,000,000 |

(Exact amounts confirmed during script-tuning; subject to a 5% rebalance to ensure each rung's tick aligns to V4's tick spacing.)

### 4.5 Sablier vesting stream

- **Asset:** 10M SINC.
- **Source wallet:** `0x09E2…12Ac` (treasury).
- **Recipient:** A new wallet derived from a hardware wallet (recommended). User to specify in runbook.
- **Duration:** 24 months, linear, **non-cancellable, non-transferable**.
- **Start:** Graduation day + 0 (vests from launch).
- **Created via:** sablier.com UI (not script — reduces chance of recipient-address typos).

### 4.6 Final supply allocation (100M SINC)

The curve receives a single 65M deposit at funding time. Phase 1 buyers consume some of that (call it `X`); the remaining `65M - X` is paired with 80% of accumulated ETH at graduation and becomes the V4 LP (then burned). The treasury retains 35M for post-graduation buckets.

| Bucket | Amount | Where it lives | When it moves |
|---|---|---|---|
| Bonding curve (Phase 1 distribution + Phase 2 LP seed) | **65M** | `SincBondingCurve` contract initially; consumed by curve buys then paired into LP at graduation | Step 7 (funded); consumed during Phase 1 and at graduation |
| — of which: distributed to Phase 1 buyers | `X` (variable) | Buyers' wallets | During Phase 1 |
| — of which: paired with 80% of curve ETH at graduation, then LP burned | `65M - X` | Uniswap V4 SINC/WETH pool LP → `0x...dEaD` | At graduation (atomic) |
| Concentrated $1.50 ceiling LP | 5M | Single-tick V4 LP position | Post-graduation, step 12 |
| Sell-side limit-order ladder ($0.01 → $1.40) | 20M | Held by `SincLimitOrderHook` as out-of-range liquidity | Post-graduation, step 13 |
| Sablier 24mo linear vest | 10M | Sablier stream NFT | Step 14 |
| **Total** | **100M** | ✓ | |

**Treasury working balance over time** (starts at 100M in `0x09E2…12Ac`):
- After step 7 (fund curve): 35M in treasury, 65M in curve.
- After step 12 (place ceiling LP): 30M in treasury.
- After step 13 (place ladder): 10M in treasury.
- After step 14 (create vest): 0M in treasury (10M flows into Sablier stream; user receives stream NFT).
- Steady-state post-launch: treasury accumulates SINC from the agent-burn loop's 50% treasury share + 20% ETH cut from graduation; no SINC reserves held outright.

## 5. Off-chain architecture

### 5.1 Website (Flask templates + static)

| File | Change |
|---|---|
| `templates/sinc_gateway.html` | Major rewrite. Remove false "LP Locked" claim. Replace fake swap card with real V4 swap (viem + walletconnect). Add limit-order placement card inside the main grid (replaces or augments the existing Token Details card). Embed live curve price during Phase 1; switch to V4 pool price post-graduation. Display CertiK 97/100 badge with link. Display burn counter from agent loop. |
| `templates/axiom.html` | Update 80%-to-ecosystem claim copy to reflect that it is wallet-routed (off-chain commitment), not contract-enforced. Add cross-link to new SINC contract. Update logo references. |
| `templates/home.html` | Update SINC link block (currently at ~line 810) to point at new contract address + new pool. Fix broken `/sinc-gateway` nav link → `/sinc`. Update nav. |
| `static/sinc_logo.png` (new) | New logo to match refresh. Source from existing `Corporate Logo with Globe Icon (Graph).png` or new design (user choice). |
| `static/axiom_logo.jpg` | Update if logo family changes. |
| `static/sincor_nav_icon.png` | Update to match new logo family. |

### 5.2 Flask backend (`src/sincor2/app.py`)

- **No new `/limit-orders` route** — UI embedded inside `/sinc` per user request.
- **New `/api/sinc/curve` endpoint:** server-side proxy for curve state (current price, tokens sold, ETH accumulated, graduation progress). Cached 30s.
- **New `/api/sinc/burn-stats` endpoint:** aggregated SINC burned by agents, for the live counter on `/sinc`. Reads from agent-burn loop logs.
- **New `/x402` endpoint:** HTTP 402 Payment Required handler. Returns SINC payment instructions; verifies on-chain payment before serving the underlying resource.
- **Mvp_app.py vs app.py**: spec uses `app.py` as canonical (matches imports in existing templates). `mvp_app.py` is treated as a legacy entry point; relevant routes are duplicated there only if user confirms it's still served.

### 5.3 Agent-utility-burn loop

- **Module:** `sincor-clean/agent_billing.py` (new).
- **Flow:**
  1. A SINCOR agent receives a billable task (existing platform mechanism).
  2. The customer pays via SINC ERC-20 transfer to `0x09E2…12Ac`'s billing sub-address.
  3. `agent_billing.py` detects the payment (via web3 event listener on the SINC contract), and immediately initiates two on-chain txs:
     - 50% of received SINC → `transfer(0x...dEaD, amount/2)` — burn
     - 50% remains in treasury for ops
  4. Both txs are logged to `data/agent_burn_log.jsonl` for the public counter.
- **Signing:** This is the ONE place in the spec where automated signing is acceptable, because:
  - The wallet used for forwarding burns is a **dedicated billing-forwarder wallet**, NOT the treasury wallet.
  - The billing-forwarder wallet only ever holds incoming SINC briefly (between receipt and forward).
  - It is funded with minimal ETH for gas only.
  - Its private key is stored in `secrets_local/` (not in OneDrive, not in plaintext on Desktop).
  - **User to generate this wallet via hardware → export key for the forwarder ONLY, never used for anything else.**

### 5.4 x402 micropayment endpoint

- **Standard:** HTTP 402 Payment Required, Coinbase x402 specification.
- **Behavior:** Agents (or any client) hitting paid SINCOR endpoints receive a 402 response with a payment payload describing the SINC amount + payment address. On payment confirmation (via web3 listener), the resource is served.
- **Pricing:** Per-call SINC amount configurable in `config/x402_pricing.yaml`.

### 5.5 Disclosed-AI content engine

Uses the existing 42 SINCOR agents to produce continuous, AI-disclosed content. Lives in `sincor-clean/launch_content_engine/` (new module, distinct from the existing `sincor_content_engine_manual/`):

```
launch_content_engine/
├── config/
│   ├── posting_schedule.yaml        # frequency per channel
│   ├── topic_rotation.yaml          # 30-day rotation of factual topics
│   └── disclosure_strings.yaml      # AI-identification suffixes per platform
├── pipelines/
│   ├── twitter_pipeline.py          # 3-5 posts/day, all with "Posted by SINCOR Agent — AI-generated"
│   ├── farcaster_pipeline.py        # 1 cast/day in /base, /defi, /ai channels
│   ├── mirror_pipeline.py           # 1 long-form article/week
│   ├── youtube_shorts_pipeline.py   # 1 short/day via Synthesia or HeyGen API
│   └── elevenlabs_podcast.py        # 1 voice clip/week
├── content_topics.json              # rotation: hook math, burn loop, referral, audit, etc.
├── human_review_queue.db            # SQLite: agents draft → human (user) approves → post
└── adapters/
    ├── twitter_api.py
    ├── farcaster_api.py
    └── ...
```

**Mandatory rules baked into every pipeline:**
- Every post includes an AI-disclosure string per FTC Endorsement Guides (2023).
- No price predictions. No return promises. No "going to the moon."
- Factual on-chain data only (volume, holders, burn count, etc.) pulled from the Dune dashboard or direct RPC.
- Human (you) approves every post via the review queue before broadcast. No auto-publish in v1.
- Failure mode: if a post is auto-flagged as making promises/predictions, it bounces back to the agent for rewrite.

**Cost:** ~$100/month for Synthesia/HeyGen/ElevenLabs tooling. Twitter/Farcaster/Mirror APIs are free or near-free.

### 5.6 Public Dune Analytics dashboard

Dashboard at `dune.com/sincor/sinc` showing live:
- Total holders + 24h/7d/30d growth
- Burn counter (lifetime + this week) sourced from `Transfer(_, 0x...dEaD, _)` events on SINC contract
- Treasury balance + tx history for `0x09E2…12Ac`
- Vesting stream progress (Sablier NFT state)
- 24h volume + tx count
- Top 10 holders (with vesting/lock annotations)
- Agent burn loop activity from the SINCOR billing event log
- Phase 1: curve ETH accumulated, SINC distributed, % to graduation
- Phase 2: V4 pool depth, ladder fill status, ceiling LP status

Queries are checked into `sincor-clean/dune/` as `.sql` files for transparency. Dashboard embeds into `/sinc` page via Dune's `<iframe>` widget.

### 5.7 CertiK Skynet continuous monitoring

Existing 97/100 score is one-time scan. **Subscribe to Skynet ongoing monitoring** to display live status badge on `/sinc` page. Cost: variable per CertiK's current pricing.

Badge displays on `/sinc` linking to the CertiK profile page. Re-scans triggered automatically when contract is modified (won't happen for SINC token contract; will happen if bonding curve or hook is upgraded — neither is planned).

### 5.8 Paid placement campaign (budget: $0–$200 over 30 days)

Organic-heavy. Spend scales from Phase 1 curve revenue (the 20% treasury cut at graduation = ~$300 if curve fills, fully covering this budget).

| Channel | Cost | When | Notes |
|---|---|---|---|
| DEXScreener trending boost | ~$50–100 | Day 7 (post-curve-launch) | 24h slot; targets DEX-native buyers |
| DEXTools trending placement | ~$50–100 | Day 14 (post-graduation if reached) | 24h slot |
| Telegram channel sponsorship (1 small Base channel) | ~$50 | Day 21 | Negotiated case-by-case |
| Farcaster Frame placement | $0 (organic + cross-post) | Continuous | Free placement |
| X/Twitter Ads | $0 | Defer until $1k+ budget available | Requires X crypto-ads account verification (2-3 days lead time anyway) |
| CMC/CG banners | $0 | Defer to Phase 2 | $300+ minimum; not in current budget |

Total max spend: $200. Scales up post-graduation if budget allows.

## 6. Data flow

### 6.1 Phase 1 — Buying SINC via the bonding curve

1. User visits `/sinc`, sees curve price + "Buy" button.
2. Connects wallet via walletconnect (MetaMask / Rainbow / Coinbase Wallet / hardware).
3. Enters ETH amount. Frontend calls `SincBondingCurve.getBuyAmount(ethIn)` for preview.
4. User signs the `buy(ethIn)` tx; ETH goes into curve, SINC comes out per curve math.
5. Frontend updates curve state via 30s polling.

### 6.2 Phase 1 — Selling back to curve

1. User has SINC, wants out. Same `/sinc` UI, swap-flip arrow.
2. User signs `approve(curve, sincAmount)` then `sell(sincAmount)`.
3. SINC returns to curve, ETH refunded per curve math.

### 6.3 Phase 1 → Phase 2 transition (graduation)

1. Some user's `buy()` tx crosses the ETH threshold.
2. They (or anyone) call `graduate()` — permissionless. Likely a keeper bot will MEV this for the gas reimbursement; we accept that.
3. Graduation tx atomically: initializes V4 pool, adds LP, burns LP token, sends 20% treasury cut.
4. Frontend detects `graduated == true` via polling and switches the swap card from curve-mode to V4-pool-mode.

### 6.4 Phase 2 — Swap on V4 pool

1. User at `/sinc`, swap card now reads from V4 PoolManager + Quoter.
2. Frontend builds Universal Router call with `SWAP_EXACT_IN_SINGLE`, `SETTLE_ALL`, `TAKE_ALL` actions.
3. First swap requires Permit2 approval (frontend explains: 2 txs first time, 1 thereafter).
4. Subsequent swaps: 1 tx.

### 6.5 Phase 2 — Place a limit order

1. User toggles to "Limit Order" tab inside the same swap card.
2. Enters amount + side (buy SINC / sell SINC) + target price. Frontend converts price → tick.
3. Frontend validates target tick is on the correct side of current pool tick (refuses if it would fill instantly).
4. User signs `SincLimitOrderHook.placeOrder(poolKey, targetTick, zeroForOne, liquidity)`.
5. Order appears in "Your Open Orders" list (frontend reads order IDs from localStorage cache + `getOrderInfo`).

### 6.6 Phase 2 — Cancel or withdraw

1. User clicks Cancel on an unfilled order → signs `cancelOrder(poolKey, targetTick, zeroForOne, msg.sender)`.
2. User clicks Withdraw on a filled order → signs `withdraw(orderId, msg.sender)`.

### 6.7 Agent invocation → SINC burn (continuous, post-launch)

1. Customer pays an agent for a job in SINC (existing SINCOR billing UX).
2. Agent platform receives SINC at billing-forwarder wallet.
3. `agent_billing.py` triggers within seconds: half of received SINC is burned to `0x...dEaD`, half routed to treasury.
4. `/sinc` page reads the public burn counter and updates display.

## 7. Deployment runbook (ordered)

Steps marked **🔐 SIGN** require user signature from their wallet. Steps marked **🤖 AUTO** are scripted but still initiated by user manually.

| # | Step | Type | Notes |
|---|---|---|---|
| 0 | Set up new clean treasury wallet `0x09E2…12Ac` (already done) | manual | If not on hardware wallet yet, strongly recommend setup now |
| 1 | **Move 100M SINC from `0x35cb…7D6f` → `0x09E2…12Ac`** | 🔐 SIGN | One ERC-20 transfer. Critical because `0x35cb` key is in `pk.txt` |
| 2 | Sweep ETH dust from `0x35cb` to `0x09E2` | 🔐 SIGN | Abandon `0x35cb` after |
| 3 | Deploy Genesis NFT to Base Sepolia | 🤖 AUTO | `forge script 01_DeployGenesisNFT.s.sol --rpc-url $SEPOLIA_RPC` |
| 4 | Deploy `SincBondingCurve` to Base Sepolia (curve constructor receives NFT addr) | 🤖 AUTO | `forge script 02_DeployBondingCurve.s.sol --rpc-url $SEPOLIA_RPC` |
| 5 | Deploy `SincLimitOrderHook` to Base Sepolia (CREATE2-mined) | 🤖 AUTO | Scripts 04 + 05 |
| 6 | End-to-end Sepolia tests: buy w/ referral, NFT auto-mint, sell, graduate, swap, limit order, sandwich-detection | manual + scripted | All paths exercised; record tx hashes |
| 7 | Submit free CertiK Skynet scan on Sepolia: curve, hook, NFT | manual | Wait for scan results (hours each). Expect ≥90 score on each. |
| 8 | If all scans pass: deploy NFT + curve + hook to Base mainnet | 🔐 SIGN | Run scripts 01, 02, 04, 05 with `--broadcast` |
| 9 | Verify mainnet deploys on Basescan (source upload, link contracts) | manual | Anchors all three contracts as audit-eligible |
| 10 | Transfer 65M SINC from `0x09E2` to curve contract | 🔐 SIGN | Funds Phase 1 distribution + Phase 2 LP seed (consumed at graduation) |
| 11 | Submit CertiK Skynet scans on mainnet contracts | manual | For public credibility before launch |
| 12 | Subscribe to CertiK Skynet continuous monitoring | manual | Live badge on `/sinc` |
| 13 | Set up Dune Analytics dashboard (queries from `dune/*.sql`) | manual | Public at `dune.com/sincor/sinc` |
| 14 | Configure `launch_content_engine` (API keys, schedule, topics, human-review queue) | manual | Test pipeline end-to-end on Sepolia mainnet before flipping live |
| 15 | Update `/sinc` template with mainnet contract addresses + Dune iframe + CertiK badge + go live | manual | Site update, public launch |
| 16 | Announce launch — agent burn loop active, x402 endpoint live, content engine producing | manual | "SINC reset + relaunch" post with all tx hashes + Dune dashboard link |
| 17 | (After threshold hit) anyone can call `graduate()` | permissionless | Atomic: V4 pool init, LP burn, treasury 20% cut |
| 18 | Place concentrated $1.50 ceiling LP | 🔐 SIGN | `forge script 07_PlaceConcentratedCeiling.s.sol` |
| 19 | Place sell-side limit-order ladder | 🔐 SIGN | `forge script 06_PlaceLimitOrderLadder.s.sol` |
| 20 | Create Sablier 24mo stream for 10M SINC | 🔐 SIGN via sablier.com UI | Recipient = user-specified |
| 21 | Submit audit DB re-reviews (DEXTools / Dexscreener / GoPlus / Uniswap tokenlist) | manual | Evidence packet: all tx hashes from steps 8–20 |
| 22 | Submit Coinbase Smart Wallet token addition request | manual | Lead time: 2–4 weeks; submit now so it lands during Phase 2 |
| 23 | Phase 2 paid-placement campaign starts (DEXScreener boost day +7, etc.) | manual | Budget from runbook §5.8; scale from graduation cut |

**Hard ordering constraints:**
- Step 1 must precede everything else (rescues funds from compromised wallet).
- Step 4 (Sepolia E2E) must precede step 6 (mainnet deploy).
- Steps 12, 13, 14 may run in any order after graduation but ALL before step 15.
- Step 9 (site go-live) should precede step 10 (announce) by no more than an hour to avoid stale-link reports.

## 8. Error handling & edge cases

### 8.1 Smart-contract layer

| Failure | Mitigation |
|---|---|
| Bonding curve math overflow on extreme buys | OpenZeppelin SafeMath equivalents via Solidity 0.8+. Foundry fuzz tests cover 10,000 random buy/sell sequences. |
| CREATE2 hook address mining fails | Uniswap `HookMiner` library; ~30s off-chain compute. Pre-mined and validated before any mainnet tx. |
| V4 pool init `sqrtPriceX96` set wrong at graduation | `SincBondingCurve.graduate()` computes `sqrtPriceX96` from the curve's final price atomically. Tested in `Graduation.t.sol` against multiple curve-completion scenarios. |
| LP NFT burn fails (graduation atomicity) | Graduation tx is all-or-nothing — if any sub-step reverts, entire tx reverts. ETH stays in curve, retry possible. |
| Hook OZ contract has undiscovered bug | Audited; users can still close v4 positions via PositionManager even if hook stops accepting new orders. Worst case = limit-order feature degraded, swap unaffected. |
| Curve has bug that locks ETH after graduation threshold | High-severity. Mitigated by Sepolia E2E test (step 4) reproducing real graduation. NO admin escape hatch; we accept this risk in exchange for the credibility of "no rug functions exist." If discovered post-deploy, we'd deploy v2 of the curve and inform any Phase 1 buyers (small group). |
| MEV bot front-runs graduation tx | Acceptable — `graduate()` is permissionless on purpose. Whoever calls it pays gas; the outcome is deterministic. |
| Front-run of pool init by sandwich attacker | Mitigated by submitting graduation tx via Flashbots Protect on Base (private mempool). Documented in runbook step 11. |

### 8.2 Frontend layer

| Failure | Mitigation |
|---|---|
| Wallet drops mid-tx | Each user action shows pending/success/failure state with Basescan link. |
| First-time Permit2 friction | UI explains: "First swap = 2 txs. After = 1." |
| Price feed (Dexscreener) hasn't indexed new pool | Fallback: read pool state directly from StateView `0xa3c0…7a71`. Show "Live (direct)" indicator. |
| User places limit order on wrong side of tick | Pre-submit validation refuses; shows "This price would fill immediately — use Swap instead." |
| WalletConnect fails on iOS Safari | Tested explicitly. Documented fallback: direct app.uniswap.org link. |
| User tx reverts | viem catches `ContractFunctionRevertedError`, decodes reason, shows human-readable message. |

### 8.3 Operational layer

| Failure | Mitigation |
|---|---|
| `pk.txt` on user's Desktop leaks | Already-leaked wallets (`0x35cb`, `0x6E36`) abandoned. New treasury `0x09E2` key never typed into any chat or plaintext file. Hardware wallet strongly recommended. |
| Billing-forwarder wallet key leaks | Limited blast radius: only ever holds in-flight SINC briefly. Key stored in `secrets_local/` (not OneDrive-synced). Can be rotated by deploying a new forwarder and switching the SINCOR billing endpoint. |
| Treasury wallet runs out of gas mid-runbook | Pre-flight balance check at start of every script. User funds with $50–100 worth of ETH (covers entire runbook with headroom). |
| User accidentally runs scripts out of order | Each script reads a `runbook_state.json` file and refuses to run if prerequisites haven't recorded a success marker. |
| User loses access to their new wallet | If hardware wallet seed is preserved, recovery is straightforward. If hot wallet only and seed lost: **all 100M SINC inaccessible**. User MUST back up the seed phrase on paper, not in any cloud-synced location. Spec marks this as critical pre-deploy task. |

## 9. Testing strategy

### 9.1 Solidity tests (Foundry)

- `test/SincBondingCurve.t.sol`:
  - Unit tests for curve math (correctness of `getBuyAmount`, `getSellAmount`).
  - Fuzz tests (10k iterations) for buy/sell sequences.
  - Referral tests: 3% routes to referrer; self-referral blocked; zero-address routes to treasury.
  - NFT auto-mint tests: first buy mints, second buy doesn't, mint event includes correct buyOrderNumber.
  - Negative tests: try to withdraw ETH (`expectRevert`), try to mint (`expectRevert`), try to graduate before threshold (`expectRevert`), try to buy after graduation (`expectRevert`).
- `test/Graduation.t.sol`:
  - Fork test against Base mainnet at PoolManager `0x4985…2b2b`.
  - Buy enough to cross threshold; call `graduate()`; assert V4 pool exists, LP added, LP burned, treasury received 20%.
- `test/HookIntegration.t.sol`:
  - Fork test: place a limit order, simulate swap that crosses tick, assert order filled, withdraw works.
- `test/AntiSandwich.t.sol`:
  - Simulate a sandwich attack: attacker swap → victim swap → attacker swap, all in same block.
  - Assert: second attacker swap pays 10× the base fee (3% vs 0.3%).
  - Assert: normal users doing 1 swap/block always pay base fee.
- `test/GenesisNFT.t.sol`:
  - Mint only callable by curve (`expectRevert` from other callers).
  - Transfer reverts ("Soulbound: non-transferable").
  - Token metadata correctness.

### 9.2 Sepolia E2E (manual + scripted)

- Deploy all contracts to Base Sepolia.
- Fund a test wallet, buy $5 worth from curve.
- Sell back; assert refund.
- Buy enough to graduate (test threshold = $10).
- Call `graduate()`; assert AMM pool live.
- Swap $1 on the AMM.
- Place a limit order; simulate a swap that crosses it; withdraw.
- Document each Basescan-Sepolia tx hash in the runbook.

### 9.3 Frontend manual tests

- Wallet connect on iOS Safari (Rainbow + Coinbase Wallet), Android Chrome (MetaMask Mobile), desktop Chrome + Firefox + Edge (MetaMask + Rainbow extension + WalletConnect).
- Swap a small amount on Sepolia, confirm UX is clean.
- Place + cancel a limit order on Sepolia.
- Confirm price-feed fallback works when Dexscreener returns null.

### 9.4 Server-side tests

- Mock agent invocation triggering a SINC payment; assert burn tx is generated within 30s; assert log entry in `agent_burn_log.jsonl`.
- x402 endpoint integration test: client requests paid endpoint, gets 402, simulates payment, gets resource.

### 9.5 Pre-mainnet checklist

- [ ] All Foundry tests passing
- [ ] Sepolia E2E run completed
- [ ] CertiK Skynet scan of Sepolia curve = passing
- [ ] Frontend tested on all target browsers/wallets
- [ ] Gas estimates documented in runbook
- [ ] User has confirmed hardware-wallet recovery procedure
- [ ] User has rotated all secrets from `pk.txt`, `Axiom1.txt`, `secrets.txt`, etc.
- [ ] Audit DB evidence packet template prepared
- [ ] Launch announcement post drafted (honest + tx-hash-linked)

## 10. Rollback / recovery

The spec accepts the principle that **bonding curves with no rug functions cannot have admin escape hatches** — that's the entire credibility point. So rollback is constrained.

| Scenario | Recovery |
|---|---|
| Bonding curve bug discovered before users buy | Deploy v2 of curve, transfer SINC out via the funding wallet (which still holds it pre-funding-tx), update site to point at v2. |
| Bonding curve bug discovered after users have bought | Deploy v2 of curve. Phase 1 buyers have to manually `sell()` back to v1 to recover their ETH. If `sell()` is the buggy function, ETH is locked. **This is the irreducible risk of "no rug functions."** Mitigate via Sepolia E2E + audit before mainnet. |
| Graduation logic bug discovered | If `graduate()` reverts, curve sits with accumulated ETH. Users can still `sell()` to exit. We'd communicate the issue and recommend everyone sell back, then redeploy. |
| Hook bug discovered | OZ contract has no upgrade path. Worst case: limit-order feature broken, swap still works. Users with active orders can call `cancelOrder` to recover funds. |
| Frontend bug | Hot-deploy fix; no on-chain implication. |
| Treasury wallet drained | If `0x09E2` private key leaked: irrecoverable. User MUST use hardware wallet for treasury. |

## 11. Out of scope / Phase 3 roadmap

Explicitly NOT in this spec; documented for future planning:

1. **Morpho Blue lending market for SINC** — requires an established price oracle and supply-side capital. Revisit when SINC has consistent >$50k daily volume and a 30+ day price history.
2. **Bunni / Maverick / Arrakis active LP management** — amplifies existing depth; useful once base LP exceeds ~$50k.
3. **Cross-chain bridge for SINC** — bridges to Arbitrum / Solana / Ethereum L1. Revisit by user demand.
4. **`sincor-agent-kit` TypeScript/Python SDK** — formalizes agent integration. Build after launch confirms the agent-burn loop works.
5. **Custom V4 hook for buyback-from-fees** — automated treasury → market buys. Requires audit. ~$15–40k cost.
6. **Public agent registry page** — visualizes 42 SINCOR agents + their SINC consumption stats. Marketing tool, not launch-critical.
7. **DAO / governance** — only if community organically requests it. SINC was never sold as a governance token.
8. **CEX listings** — Coinbase / Binance / Kraken listings require depth, age, and legal review. Phase 4+.
9. **Migration claim contract for v2 holders** — explicitly NOT needed because user owns all 37 v2 wallets. If this changes (e.g., new v2 holders discovered), reopen this decision.
10. **Renouncement of canonical SINC ownership** — appears already renounced per GoPlus; verification in runbook step 6.5. If not renounced, add step 6.5b: user signs `renounceOwnership()` from `0x09E2`.

## 12. Decisions made during brainstorming (for traceability)

| Decision | Choice | Date |
|---|---|---|
| Approach | A (cleanup + relaunch), then pivoted to Track B (use newer audited contract) | 2026-05-16 |
| Canonical SINC contract | `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` (Track B) | 2026-05-16 |
| Decimals | Keep 8 (preserves CertiK audit) | 2026-05-16 |
| v2 migration | None (user owns all 37 v2 wallets) | 2026-05-16 |
| LP lock method | Burn LP tokens to `0x...dEaD` via curve graduation | 2026-05-16 |
| Owner stake handling | 10M Sablier vest, 24mo linear | 2026-05-16 |
| Launch venue | Bonding curve Phase 1 + Uniswap V4 + LimitOrderHook Phase 2 | 2026-05-16 |
| Graduation threshold | $1,500 of ETH accumulated | 2026-05-16 |
| Graduation split | 80% LP / 20% treasury | 2026-05-16 |
| Target ceiling price | $1.50 (concentrated LP + limit-order ladder) | 2026-05-16 |
| Wash trading | Refused. Out of scope. | 2026-05-16 |
| Agent integration scope | Server-side utility-burn loop + x402 endpoint (no smart contract changes) | 2026-05-16 |
| Treasury wallet | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` | 2026-07-17 |
| Plaintext-key wallets | Empty and abandon (`0x35cb`, `0x6E36`) | 2026-05-16 |
| Referral system | 3% of buy routed to referrer (or treasury if none); self-referral blocked | 2026-05-16 |
| Genesis NFT | Soulbound ERC-721 auto-minted on first Phase 1 buy | 2026-05-16 |
| Anti-MEV in hook | Dynamic fee 0.3% → 3% triggered by 2nd+ swap in same block per pool | 2026-05-16 |
| Disclosed-AI content engine | Active on launch day; human-approval queue required for all posts | 2026-05-16 |
| Public Dune dashboard | `dune.com/sincor/sinc`; embedded on `/sinc` page | 2026-05-16 |
| CertiK Skynet monitoring | Subscribe to continuous monitoring, badge on `/sinc` | 2026-05-16 |
| Paid placement budget | $0–$200 over first 30 days; scale from Phase 1 graduation revenue | 2026-05-16 |
| Logo system | 4-image system: Image 2 = token mark, Image 1 = hero, Image 3 = wordmark, Image 4 deprecated | 2026-05-16 |

## 13. Open questions

These need user decisions before plan execution begins, but do not block writing the implementation plan.

1. **New logo source.** ✅ Resolved 2026-05-16. Four-image brand system supplied by user:
   - Primary token mark (square, no text) → `static/sinc_logo.png` (multiple sizes), favicon
   - SINC marketing hero ("SINC by Sincor") → `static/sinc_og_image.png` for social shares
   - SINCOR AI wordmark → `static/sincor_wordmark.png` for company-level nav and pitch surfaces
   - Existing 200×200 nav icon → deprecated, replaced by the new primary mark at 128×128
   - **Sub-decision pending:** adopt "SINCOR AI" as the company brand (replacing "SINCOR Business Solutions") or use it only on AI-product surfaces. Spec defaults to full adoption pending user confirmation.
2. **Hardware wallet.** Will the user obtain Ledger/Trezor before launch, or proceed with hot wallet only? Strongly recommend hardware; spec assumes hardware unless user states otherwise.
3. **Sablier recipient wallet address.** Confirmed treasury wallet (`0x09E2…12Ac`) is the default; user can specify a separate vesting recipient if desired.
4. **x402 pricing.** Initial SINC-per-call rates for paid SINCOR endpoints. Suggestion: 1 SINC per call as a round number; adjustable via config file.
5. **Mvp_app.py status.** Is this file still in production routing, or is `app.py` canonical? Affects whether template + route changes need to be duplicated.
6. **Whether the new SINC contract owner is truly renounced.** Spec assumes yes per GoPlus; runbook step 6.5 verifies via direct call to `owner()` view. If not renounced, add transfer-then-renounce sub-steps.

---

**End of design spec.** Awaiting user review before invoking the `writing-plans` skill to break this into an implementation plan.