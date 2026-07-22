# Moebius v2 — Sealed-Bid Escrow

## What changed and why

v1 collected its "tax" by requiring the searcher to *declare* its own profit and
then pay 20% of that declaration to LPs, with a `nonReentrant` guard it didn't
need (the function had no external calls). Three structural problems:

1. **Self-declared profit is gameable to dust.** A searcher atomically wraps its
   real profit in a secondary tx, calls `executeMEV` with `declaredProfit = 0`,
   and pays nothing.
2. **Balance-sniffing enforcement is griefable.** `totalRepaid >=
   principal + lpTax` reads the *hook's* token balance; anyone can dust the hook
   and satisfy the invariant for the searcher.
3. **No auction.** One searcher per tx, no competition, tax decoupled from the
   true value of the flow.

v2 replaces the honor system with an **escrowed sealed bid**:

1. Searcher escrows a hard `bid` in the pool's **real asset** up front
   (`transferFrom` / `msg.value`). No bid, no flash credit.
2. Hook flash-mints pMEV working capital inside `unlockCallback`.
3. Searcher runs its loop and must return (burn) **100%** of the pMEV —
   enforced by the token, which reverts the whole tx (escrow included) on
   shortfall.
4. The escrowed bid is split deterministically: **80% donated to in-range LPs
   via `PoolManager.donate`, 20% paid to protocol treasury.**

Competition between searchers for the same flow pushes bids toward true arb
value. The bid IS the tax.

## Policy levers

| Lever | Function | Notes |
|---|---|---|
| Policy rate floor | `setMinBidBps(bps)` | `bid >= flashAmount * minBidBps / 10_000`; capped at 10% |
| Pool swap fee | `setPoolSwapFee(key, fee)` | pMEV pools must be dynamic-fee; owner sets the fee |
| Treasury | `setTreasury(addr)` | receives 20% of every escrowed bid |

## Deploy invariants

- Hook address must carry **zero** lower-14 hook-flag bits → CREATE2-mine the
  salt (see `DeployMoebius.s.sol`).
- pMEV pools must be initialized with the **dynamic fee flag** (`0x800000`).
- `PhantomCreditToken` is deployed with the *target* hook address, so mine the
  hook address first, then deploy the token with it.
- `seedLiquidity` (owner-only) gives pMEV its price: the hook flash-mints
  inventory, pairs it with the owner's real asset, and holds the LP position.

## Test coverage

`onchain/test/MoebiusMEVHook.t.sol` — 10 cases: bid floor, fee-on-transfer
rejection, escrow exactness, full-burn enforcement, LP/treasury split,
policy-rate admin, pool-fee lever, seed liquidity, rescue, ownership.
