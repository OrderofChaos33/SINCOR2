# Deploy Package — P22 Concentrated Liquidity Manager

PACK-3. Instructions only. **No deployment has been performed for this
package, and none is performed by this document.** This module is not
separately deployed today; the steps below are the drill a deployer
would run *inside a signed counterparty engagement* (see
`../ENGAGEMENT.md`), on the counterparty's Sepolia environment first.

State: EXTERNAL — a buyer/counterparty is required before any step here
is run for real.

## 1. Dependency list

Runtime:

- Python >= 3.11
- Python standard library only — the package declares **zero**
  third-party runtime dependencies (`dependencies = []` in
  `../pyproject.toml`; full inventory in `../DEPENDENCIES.md`).

Test/drill:

- `pytest >= 8` (the `test` extra in `../pyproject.toml`).

External, engagement-supplied (not bundled):

- A price-oracle adapter satisfying the duck-typed contract in
  `src/p22_clmm_manager/price_oracle_stub.py` (`get_price(asset, now,
  strict=False)` returning an object with `price_fp`). The package
  ships only the stub surface; it wires no feed of its own.
- Sepolia RPC access for the counterparty's environment, if the
  engagement's integration layer reads chain state. The extracted core
  itself makes no chain calls.

## 2. Config surface

Every environment variable / config key the module reads. All env vars
are read **at import time** by the core module, so set them before the
process starts; changing them requires a restart.

| Env var | Default | Meaning |
|---|---|---|
| `TREASURY_ADDRESS` | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` | Address recorded as the recipient of the treasury cut in proceeds distribution. Set explicitly to the engagement's agreed treasury address during the drill. |
| `CLMM_VOL_BAND_TICKS` | `240` | Volatility band: maximum per-shift tick bound and maximum range width (ticks). |
| `CLMM_TICK_SHIFT_STEP` | `60` | Tick distance shifted per tick-shift response. |
| `CLMM_JIT_SENSITIVITY_BPS` | unset (falls back to legacy var) | JIT flag threshold in basis points. When set, it wins. Canonical value: `9500` (95%). |
| `CLMM_JIT_SENSITIVITY` | `0.95` | Legacy form of the threshold, parsed exactly via `Decimal` (never float) into basis points. Prefer `CLMM_JIT_SENSITIVITY_BPS`. |
| `CLMM_COOLDOWN_BLOCKS` | `10` | Minimum blocks between tick shifts of one position. |
| `CLMM_MAX_SHIFT_TICKS` | `480` | Cumulative cap on tick shifts per position. |
| `CLMM_JIT_FEE_CAP_BPS` | `100` | Cap on the transient JIT surcharge (100 bps = 1%). |
| `CLMM_DRY_RUN` | `1` | Dry-run mode. Any value other than `0` keeps dry-run on. Setting `0` alone does NOT enable live positions (see the auditor gate below). |

Code constants (not env-configurable; changing them is a code change
requiring review inside the engagement):

| Constant | Value | Meaning |
|---|---|---|
| `TREASURY_CUT_BPS` | `15` | 15 bps (0.15%) of *captured JIT proceeds* routed to the treasury by `ProceedsDistributor`. Internal protocol math — not the engagement service fee. |
| `LIVE_LP_AUDITOR_GATE` | `False` | Auditor gate for opening live positions. Ships `False`; `VolRangeAgent.open_live_position` raises `LiveLPBlockedError` while it is `False`. It is flipped only with a signed auditor config inside an engagement — never as part of this drill's dry-run phase. |
| `SERVICE_FEE_BPS` (in `service_fees.py`) | `1500` | The engagement service form: 15% of earned LP trading fees. Commercial accounting constant, matching `../ENGAGEMENT.md`. |

Per-pool config keys (`PoolConfig`, set in code by the integrator):
`pool_id` (required), `tick_spacing` (default `60`), `fee_tier_bps`
(default `30`), `vol_band_ticks`, `tick_shift_step`,
`jit_sensitivity_bps`, `cooldown_blocks`, `max_shift_ticks` (defaults
from the env-derived constants above).

A starting template is in `config.example.env` next to this file.

## 3. Sepolia drill (inside a signed engagement only)

Preconditions: a signed engagement exists (see `../ENGAGEMENT.md`); the
counterparty has named the Sepolia pools/positions in scope; the
engagement's treasury address is agreed in writing.

1. Confirm the signed engagement covers Sepolia integration work for
   the named pools. If there is no signed engagement, stop — this drill
   is not run speculatively.
2. Clone the repository at the agreed commit and check out this package
   (`packages/defi/p22-clmm-manager/`).
3. Create a clean virtual environment with Python >= 3.11 and install
   the package with its test extra:
   `pip install -e ".[test]"` from the package directory.
4. Copy `config.example.env` to the deployer's environment file; set
   `TREASURY_ADDRESS` to the engagement's agreed treasury address and
   confirm `CLMM_DRY_RUN` is `1`. Leave every other value at its
   default unless the engagement's scope document says otherwise.
5. Run the full package test suite (`python -m pytest tests/ -q`) and
   record the result in the engagement log. The drill does not proceed
   on a red suite.
6. Wire the counterparty's Sepolia price-oracle adapter to
   `price_feed_for(oracle)` (the stub contract in
   `price_oracle_stub.py`) in the engagement's integration layer —
   outside this package. Confirm `feed.assets` returns
   `["ETH/USD", "USDC/USD"]` and that an undeclared asset fails closed.
7. Instantiate `PoolConfig` for each in-scope Sepolia pool with the
   pool's actual `tick_spacing` and `fee_tier_bps`; keep
   `vol_band_ticks`, `tick_shift_step`, `cooldown_blocks`, and
   `max_shift_ticks` at defaults unless the scope document overrides
   them.
8. Replay a recorded Sepolia event window (adds, removes, swaps)
   through `JITDetector.detect` and confirm the flags match the
   counterparty's expectations for that window; record any mismatch as
   an engagement finding, not a silent config change.
9. Exercise `TickShiftResponder` and `VolRangeAgent.emit_rerange_intent`
   against the replay results. Confirm every emitted intent has
   `dry_run == True` and `executed == False`, and that
   `VolRangeAgent.open_live_position` raises `LiveLPBlockedError`.
10. Exercise `ProceedsDistributor` on the replay's captured values:
    confirm `distributed_wei + treasury_wei == proceeds_wei` exactly,
    and that claims are pull-based and pay out at most once.
11. If the engagement uses the service-fee form, run
    `ServiceFeeLedger` against the counterparty's earned-LP-fee totals
    for the drill window and confirm the accrued fee is exactly 15% of
    the recorded earned total (integer floor).
12. Write the drill report (config used, test result, replay findings,
    intent samples) into the engagement record. Any move beyond dry-run
    — flipping the auditor gate, executing intents, or pointing the
    integration at a non-Sepolia environment — is a separate, separately
    signed step outside this drill.
