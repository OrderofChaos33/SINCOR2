# DEPLOY.md — P25 DeFi Risk Mutual (PACK-3)

Deploy package for the standalone P25 (suite P05) risk-mutual module.
**Instructions only — nothing in this sprint was deployed.** Any
deployment happens only inside a signed counterparty engagement (see
ENGAGEMENT.md), on the counterparty's infrastructure and accounts, and
only after the capitalization requirements in CAPITALIZATION.md are met.
State stays EXTERNAL.

## 1. What would be deployed

The module is a Python reference build (`insurance_mutual.py`, 516 lines,
stdlib-only). It simulates the mutual's underwriting, pool accounting,
claims, and governance logic off-chain. It contains **no chain client, no
contract deployment code, no key handling, and no fund movement**: the
Sepolia drill below exercises the reference logic against a Sepolia
engagement context (addresses, block height, treasury) — it does not
broadcast transactions, because the module has no broadcast path. An
engagement that requires on-chain contracts must scope that work
separately; it is not part of this package.

## 2. Config surface

Every environment variable / config key the module reads, with defaults.
Both are read **once, at import time** (`os.getenv` at module top level);
changing them requires a process restart.

| Variable | Default | Read as | Effect |
|---|---|---|---|
| `TREASURY_ADDRESS` | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` | `TREASURY` constant; default `treasury` for `MutualPool()` | Address recorded as the recipient of the 25 bps premium fee (`FEE_BPS = 25`) forwarded at premium collection. |
| `MUTUAL_DRY_RUN` | `"1"` | `DRY_RUN = os.getenv("MUTUAL_DRY_RUN", "1").strip() != "0"` | Declares dry-run mode (the module's default posture). Honest limitation: in this reference build no method branches on `DRY_RUN`; behavior is dry-run by construction (no chain/fund code exists to switch on). Treat the flag as a posture declaration, not a runtime switch. |

No other env vars, files, or config keys are read. There are no API keys,
no RPC URLs, and no secrets in the module. An annotated copy of this
surface is in `deploy/env.example`.

Code constants an engagement must fix before any cover is contemplated
(not env-configurable; changing them is a code change under the
engagement): reserve ratio 130/100, premium base 200 bps / cap 1,500 bps,
fee 25 bps, claim window 30 days, review window 14 days, assessor quorum
multiplier 5×, staleness 7,200 blocks, governance quorum 10% / yes 60%,
timelock 48 h. Full list with enforcement notes: CAPITALIZATION.md.

## 3. Dependencies

Runtime: **none beyond the Python standard library** (Python ≥ 3.11).
Full inventory in PACKAGING.md §PACK-1. Test-only: `pytest ≥ 8`
(`pip install -e ".[test]"`).

## 4. Sepolia drill (inside a signed engagement only)

Preconditions: signed engagement in place; counterparty-designated
Sepolia treasury address; capitalization plan agreed per
CAPITALIZATION.md. The drill validates the reference logic end-to-end in
simulation against Sepolia parameters. It moves no funds.

1. Clone the SINCOR2 repo at the packaged commit and enter the package:
   `cd packages/defi/p25-risk-mutual`.
2. Create an isolated environment and install the package with test
   extras: `python -m venv .venv && .venv/bin/pip install -e ".[test]"`.
3. Run the full package suite and record the result:
   `.venv/bin/python -m pytest tests/ -q` — expected: 56 passed.
4. Set the engagement config in the drill shell (restart the interpreter
   after any change; both vars are import-time reads):
   `export TREASURY_ADDRESS=<counterparty Sepolia treasury>` and keep
   `export MUTUAL_DRY_RUN=1`.
5. Record the current Sepolia block height from the counterparty's
   explorer/RPC of choice; this is the `current_block` input for the
   drill's underwrite calls (the module itself makes no RPC calls).
6. In a Python session, instantiate `MutualPool()` and confirm the live
   gate: `pool.live_underwriting` is `False`, and a `buy_cover` attempt
   raises `LiveBlockedError`. Underwriting stays blocked at this step.
7. Capitalize the drill pool in simulation only:
   `pool.deposit_capital("drill-lp", <planned reserves in 6dp wei>)`,
   sized per CAPITALIZATION.md (reserves ≥ 130% of the planned
   outstanding cover).
8. Publish a drill risk score with `Underwriter().publish(...)` stamped
   at the block recorded in step 5, fetch it with `get(...)`, and confirm
   a score 7,201 blocks old raises `StaleScoreError`.
9. Flip the release flag in the drill session only
   (`pool.live_underwriting = True` — in a real engagement this step is
   the auditor + actuarial + founder release, not a code default), then
   underwrite one drill cover and verify: premium math matches
   `Underwriter.premium_wei(...)`, `treasury_collected_wei` equals
   25 bps of the premium, and a cover that would breach the 130% floor
   raises `ReserveBreachError`.
10. Exercise one drill claim end-to-end: appoint assessors, file with an
    allowlisted attestation inside the 30-day window, assess with
    quorum weight > 5× cover, and confirm the pull-based payout and the
    pause behavior (`pause_underwriting` blocks new covers, never the
    in-window payout).
11. Tear down: discard the drill environment. Nothing was broadcast,
    no funds moved, and the pool existed only in process memory.
12. File the drill transcript (test output + step 6–10 results) as the
    engagement's pre-deployment evidence. Any step beyond this drill —
    contracts, real capitalization, real cover — is a separate,
    separately approved engagement phase.

## 5. Hard stops

- No deployment outside a signed engagement.
- No mainnet step of any kind is described or authorized here.
- No cover may be represented as available until capitalization per
  CAPITALIZATION.md is in place and the live release has been given
  under the engagement.
