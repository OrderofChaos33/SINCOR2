# Moebius v2 — Sealed-Bid Rewrite (2026-07-17)

## Why the rewrite

v1's tax was levied on the searcher's **self-declared** `expectedProfit`, enforced
only by sniffing the hook's own token balance before/after. Two fatal problems:

1. **Honor system**: declare 1 wei, pay dust, keep the arb. No competition = no tax.
2. **Griefable accounting**: anyone could dust the hook with the real asset and
   distort `balanceBefore`, DoS-ing `executeMEV` or corrupting tax math.

## What changed

| Area | v1 | v2 |
|---|---|---|
| Tax basis | Declared `expectedProfit` | Hard **escrowed bid**, pulled upfront via transferFrom / msg.value |
| Enforcement | Balance sniffing | Deterministic split of escrow: 80% LP donation / 20% treasury |
| Under-declaration | Possible | Meaningless — the bid IS the tax; searchers compete on bid size |
| Policy rate | None | `minBidBps` floor: bid >= flashAmount * rate (scales with capital used) |
| Swap fee | N/A | `setPoolSwapFee` — hook owns dynamic-fee pMEV pools (lever #2) |
| Pool config | Static fee | **Dynamic fee flag (0x800000) required** — zero-flag hooks are only valid with dynamic fee pools |
| Deployment | Not specified | CREATE2 salt mining for zero lower-14 flag bits + nonce prediction for the pMEV/hook circular dependency |
| Reentrancy | Transient guard on entry | Same + typed unlock actions (MEV / SEED), nested-unlock payload revalidation |
| FoT tokens | Unhandled | Rejected at escrow (`FeeOnTransferNotSupported`) |

Also new: `seedLiquidity` — the central-bank desk. The hook flash-mints pMEV
inventory and pairs it with the owner's real asset into an LP position the hook
owns. This is what gives pMEV a price and searchers a venue. Unabsorbed pMEV is
burned; unspent real asset is refunded.

## Test results (forge test, solc 0.8.26, cancun)

10/10 passing, including:
- full cycle: flash mint -> pool sell -> rebuy -> burn -> 20/80 bid split
- bid below policy floor reverts
- non-returning searcher: entire tx reverts, escrow returned (atomicity)
- reentrant executeMEV blocked
- only hook can mint/burn pMEV
- admin gates on policy rate / rescue

## Activation runbook

1. `forge script script/DeployMoebius.s.sol --rpc-url $BASE_RPC --broadcast --verify`
   (env: DEPLOYER_PRIVATE_KEY, POOL_MANAGER=0x498581fF718922c3f8e6A244956aF099B2652b2b,
   TREASURY, MIN_BID_BPS)
2. Initialize pMEV/<realAsset> pool: `fee = 0x800000`, `hooks = <hook>`, chosen tickSpacing.
3. Owner: `setPoolSwapFee(key, fee)`.
4. Owner: `seedLiquidity(key, tickLower, tickUpper, liquidity, maxReal, amountPmev)`.
5. Ship a reference searcher (see test/mocks/MockSearcher.sol for the settlement pattern).

## Still open before mainnet funds

- External audit (this is a reserve-holding contract).
- `owner` should be a multisig, not an EOA.
- Native-asset (ETH) realAsset path is implemented but only ERC20 is covered in tests.
- Donate reverts if the pool has no in-range liquidity at current tick — keep seed range wide.
