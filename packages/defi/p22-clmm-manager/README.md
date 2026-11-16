# P22 — Concentrated Liquidity Manager (suite name P02)

Concentrated-liquidity position management module for v3/v4-style pools,
extracted as a standalone package from the SINCOR2 platform module
`src/sincor2/defi/clmm_manager.py` (SKU `SINCOR-DEFI-P02-CLMM`).

**Status: EXTERNAL.** This module exists as a platform module; it is not
separately deployed. A buyer/counterparty is required, and deployment
happens only inside a signed engagement. See `ENGAGEMENT.md` for the
engagement terms and `PACKAGING.md` for the packaging record.

## What it does

The core is a Python reference implementation of the manager logic:

- `JITDetector` — flags atomic same-block add/remove liquidity sequences
  around a swap (the JIT attack pattern), with an integer-exact
  sensitivity threshold (no float in the flag decision).
- `TickShiftResponder` — shifts a managed position's ticks away from an
  attack range, bounded by a volatility band, a cooldown, and a
  cumulative max-shift guard. Emits dry-run intents only.
- `TransientFee` — surcharge on flagged JIT providers, capped, with math
  isolated so a failure degrades to a zero surcharge and can never block
  a swap.
- `ProceedsDistributor` — captured proceeds distributed pro-rata by
  passive liquidity-seconds to LPs holding through the attack block;
  pull-based claims; 15 bps of captured proceeds routed to the treasury;
  integer-exact conservation.
- `VolRangeAgent` — realized-volatility range-width computation; emits
  dry-run re-range intents.
- `AccessControl` — manager/guardian roles; a defense pause never blocks
  user withdrawals or claims.

Safety posture of the core (unchanged from the platform original):
default mode is dry-run — intents are emitted, never executed; nothing
in this package touches a chain, a pool, or funds; opening a live
position is hard-blocked until an auditor gate constant is flipped with
a signed config; all money math is integer-exact (wei).

The package also carries `p22_clmm_manager.service_fees`, the
engagement-level accounting for the secondary service form: 15% of
earned LP trading fees (see `ENGAGEMENT.md`). That 15% is a different
quantity from the core module's internal 15 bps treasury cut on
captured JIT proceeds; the two are never conflated.

## Layout

```
src/p22_clmm_manager/clmm_manager.py     extracted core (see PACKAGING.md)
src/p22_clmm_manager/price_oracle_stub.py  vendored minimal oracle surface
src/p22_clmm_manager/service_fees.py     15%-of-earned-LP-fees accounting
tests/                                   adapted platform tests + added tests
deploy/DEPLOY.md                         config surface + Sepolia drill
ENGAGEMENT.md                            engagement scope + priced terms
PACKAGING.md                             PACK-1..PACK-5 record
DEPENDENCIES.md                          dependency inventory
```

## Run the tests

```
python -m pytest tests/ -q
```

(requires only `pytest`; the package itself has no runtime dependencies
beyond the Python >= 3.11 standard library.)
