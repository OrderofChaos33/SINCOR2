# P21 — Yield Aggregator Vault (suite name P01)

Risk-aware capital allocator that routes deposits across yield sources
inside the SINCOR platform: Morpho Gauntlet USDC Prime and Aave v3 USDC
supply (both on Base), a USDC cash reserve, and two further registered
sources that ship disabled (see `yield_aggregator.py` strategy table).

Exists as a platform module; **not a standalone product**. This package
is the standalone extraction prepared for a counterparty engagement.
State: **EXTERNAL** — a buyer/counterparty is required, and any
deployment happens only inside a signed engagement (see
`ENGAGEMENT.md`). Nothing in this package is deployed by virtue of
being packaged.

## Layout

- `src/p21_yield_aggregator/yield_aggregator.py` — the allocator,
  extracted verbatim from the platform source (provenance in
  `PACKAGING.md`).
- `src/p21_yield_aggregator/fees.py` — engagement fee accounting at the
  priced terms (10% performance fee, 0% management fee).
- `tests/` — ported platform tests plus fee and packaging smoke tests.
- `DEPLOY.md`, `deploy/example.env` — deployment drill for use inside
  a signed engagement. Instructions only; no deployment is performed
  by this package.
- `ENGAGEMENT.md` — scope and priced-terms template.
- `PACKAGING.md` — the PACK-1 → PACK-5 record.

## Quick check

```bash
python -m pytest tests/   # 51 tests, standard library only at runtime
```

Default mode is dry-run: the allocator builds rebalance plans and
never broadcasts anything. See `DEPLOY.md` for the full config surface.
