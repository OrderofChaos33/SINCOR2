# P25 — DeFi Risk Mutual (suite name P05)

On-chain risk-mutual / coverage pool module, extracted as a standalone
package from the SINCOR2 platform repo
(`src/sincor2/defi/insurance_mutual.py` at commit `5d20c8fa`).

**State: EXTERNAL.** This module exists as a platform module; **no mutual
is capitalized or open.** Nothing in this package is deployed, holds
funds, or writes cover. Deployment and any cover happen only inside a
signed counterparty engagement — see `ENGAGEMENT.md`.

## What the module does (reference build)

The packaged module is a Python reference simulation of a Nexus-style
mutual, with SINAX-shaped claims:

- `Underwriter` — risk scores 0..1 from audit / TVL / exploit / velocity /
  oracle inputs (30/20/25/15/10 weights); tiered premium formula
  (200 bps base × tier multiplier 0.5×/1.0×/2.0×, 1,500 bps cap);
  7,200-block score-staleness guard enforced on the underwrite path.
- `MutualPool` — stablecoin capital pool with ERC-4626-style shares;
  reserve-ratio gate (reserves ≥ 130% of outstanding cover) enforced on
  every underwrite and redemption; 25 bps of every premium forwarded to
  treasury at collection; pro-rata haircuts to the wei on shortfall;
  pull-based payouts; guardian pause never blocks in-window payouts.
- Claims — 30-day claim window, assessor-allowlist attestation,
  double-claim protection, assessor quorum (weight > 5× cover) with
  14-day review and bond slashing.
- `Governance` — protocol listings (10% quorum, 60% yes) and a 48-hour
  timelock on parameter changes.
- Live gate — underwriting is blocked (`LiveBlockedError`) until the
  pool's `live_underwriting` flag is released; it defaults to blocked.

Default mode is DRY_RUN. The module touches no chain and moves no funds.

## Layout

```
packages/defi/p25-risk-mutual/
├── README.md            this file
├── PACKAGING.md         PACK-5 handoff record (what existed / was packaged / remains)
├── ENGAGEMENT.md        PACK-4 engagement scope template + priced terms
├── CAPITALIZATION.md    capitalization requirements spec (sprint extra)
├── DEPLOY.md            PACK-3 deploy package (config, dependencies, Sepolia drill)
├── deploy/
│   └── env.example      annotated config surface (names + defaults, no secrets)
├── pyproject.toml       standalone package metadata (zero runtime dependencies)
├── src/sincor_p25_risk_mutual/
│   ├── __init__.py      public API re-exports
│   └── insurance_mutual.py   the module, verbatim from the platform tree
└── tests/
    ├── test_p05_insurance_units.py  platform suite, imports rewired (28 tests)
    ├── test_p25_additional.py       added core-path coverage (25 tests)
    └── test_packaging_smoke.py      standalone-import + dependency-inventory tests (3 tests)
```

## Quick start (local evaluation only)

```bash
cd packages/defi/p25-risk-mutual
python -m pip install -e .          # or just put src/ on PYTHONPATH
python -m pytest tests/ -q          # 56 tests
```

## Commercial terms (summary — ENGAGEMENT.md governs)

- One-time engagement: **$36,250**.
- Secondary service form: cover premium **2.5%–3.25% of cover amount per
  year**, set per engagement within that band.
- Payment-rail form: scoped integration/deployment engagement, per
  counterparty.

No coverage is promised by this package, and no results are claimed.
