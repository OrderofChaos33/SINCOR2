# P24 — MEV Protection & Capture (suite P04): Deployment Guide

Status: EXTERNAL. This module is not separately deployed today. A
buyer/counterparty is required, and deployment happens only inside a
signed engagement (see ENGAGEMENT.md). This document is instructions
only — it authorizes nothing and performs no deployment.

Scope reminder: this package supports a protection / integration
engagement only. It makes no MEV capture revenue claims. SINCOR's own
backrun lane was separately measured and closed; nothing in this guide
enables, configures, or operates a capture capability.

## What is being deployed

A Python reference module (`p24_mev_protection.mev_capture`) providing:

- per-order protection evaluation (`ProtectionPolicy`): an opt-in
  100 bps slippage guardrail against the quoted price and a
  toxic-block revert for protected orders;
- swap-flow metering and sandwich / backrun *detection* over recorded
  or observed flow (`FlowMeter`, `MEVDetector`), behind a flow gate
  ($1,000 notional AND 50 bps impact by default);
- a signer deny-list (`SignerRegistry`) that fails closed if a
  treasury EOA is ever offered as a signer, plus a config audit that
  walks deployment configuration for the same condition;
- dry-run-only submission behaviour: bid intents are data, never
  executed; the live submission path is gated (`LiveGate`) and the
  module ships no execution path at all.

The module is a reference build for design validation and simulation.
It touches no chain, no relay, and no funds by itself. Integration
work inside an engagement connects its protection and detection
surfaces to the counterparty's own order flow and infrastructure.

## Dependencies

Runtime: **none.** Python >= 3.11, standard library only
(`hashlib`, `logging`, `os`, `time`, `collections`, `dataclasses`,
`typing`). See PACKAGING.md for the full dependency inventory.

Test-only: `pytest >= 8` (`pip install -e ".[test]"` or any pytest 8+
environment).

Install (inside the engagement environment):

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install ./packages/defi/p24-mev-protection
# or, for a source checkout without installing:
#   the package tests run from the package directory as-is
#   (tests/conftest.py puts src/ on the path)
```

## Configuration surface

The module reads exactly five environment variables, all at import
time. This is the complete configuration surface — the module reads
no other environment variables, no config files, and no network
endpoints. `tests/test_packaging_smoke.py` pins this list against the
source so this table cannot silently drift.

| Variable | Default | Meaning |
|---|---|---|
| `TREASURY_ADDRESS` | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` | Treasury address used as the signer deny-list entry and the routing destination in the reference accounting surface. Set it to the counterparty-agreed value inside the engagement; the deny-list behaviour is the point — this address must never appear in any signer/key field. |
| `MEV_FLOW_THRESHOLD_USD` | `1000.0` | Flow gate, notional limb: blocks below this USD notional are treated as noise by detection. |
| `MEV_IMPACT_FLAG_BPS` | `50` | Flow gate, impact limb: price displacement (bps, absolute) at or above this value is required before detection flags anything. |
| `MEV_MIN_FLOAT_USD` | `100.0` | Minimum float for constructing the reference `Bidder` object in simulation. Simulation bookkeeping only; no funds move. |
| `MEV_DRY_RUN` | `1` | Dry-run marker. Any value other than `0` keeps dry-run on. Keep at `1` for all engagement work — see the stop condition below. |

A ready-to-copy version of this surface is in `deploy/env.example`.

Fixed code constants (not environment-configurable; changing them is a
code change inside the engagement, reviewed as such):

| Constant | Value | Meaning |
|---|---|---|
| `SLIPPAGE_GUARDRAIL_BPS` | 100 | Default protected-order slippage guardrail. A per-instance `ProtectionPolicy(guardrail_bps=...)` may set a tighter value for a specific integration. |
| `FEE_BPS` | 20 | Reference accounting split used by `TreasuryRouter` (20 bps + residual). Accounting model only. |
| `BID_SHADING_MULT` | 3.0 | Simulation rule: a dry-run bid intent is emitted only when expected net >= 3x gas cost. |
| `BACKRUN_MIN_CAPTURE_PCT` | 0.30 | Detection signature parameter: reversal share of displacement required for a backrun *detection flag*. A detection parameter, not a revenue parameter. |
| `ESTIMATE_TOLERANCE` | 0.01 | Estimate-vs-actual reconciliation tolerance (1%) in the reference ledger. |
| `GAS_BUDGET_PER_SWAP` | 5000 | Hot-path metering budget in the transient-storage gas model (modelled at 600 gas per swap). |
| `MAX_FLOAT_PCT` | 0.20 | Float cap parameter in the reference model. |

### Protected RPC / stream — read this before configuring anything

This module has **no RPC endpoint, stream URL, or network
configuration of any kind** — there is no variable to set because the
module opens no network connection. Where an engagement includes an
underlying protected RPC/stream, that service is the counterparty's
own infrastructure choice, procured separately and priced as a
pass-through at cost ($0–$300 as listed; see ENGAGEMENT.md). Wiring
the counterparty's protected routing into their order flow is
integration work performed inside the engagement; it is not a setting
inside this package.

## Sepolia drill (inside a signed engagement only)

A numbered rehearsal a deployer runs with the counterparty on Sepolia
before any production integration discussion. The drill validates
configuration, protection behaviour, and key hygiene. It moves no
funds and submits nothing.

1. **Confirm the gate.** Verify a signed engagement is in place naming
   the counterparty, the integration surfaces, and the Sepolia scope.
   No signed engagement, no drill.
2. **Provision the environment.** Python 3.11+, fresh virtualenv,
   package installed per above. Record the package version
   (`p24_mev_protection.__version__`) in the drill log.
3. **Set configuration.** Copy `deploy/env.example` to the drill
   environment, set `TREASURY_ADDRESS` to the value agreed in the
   engagement, set the flow-gate variables if the counterparty's flow
   profile calls for non-default thresholds, and confirm
   `MEV_DRY_RUN=1`.
4. **Run the package test suite.** From the package directory:
   `python -m pytest`. Expected: the full suite green (68 tests at
   packaging time; record the actual count in the drill log).
5. **Audit the configuration.** Run `SignerRegistry().audit_config()`
   over the drill/deployment configuration. Expected: no exception —
   the treasury address appears in no signer/key/EOA/wallet field.
   Any `TreasurySignerError` stops the drill until the configuration
   is corrected.
6. **Exercise detection on recorded flow.** Feed recorded or
   counterparty-supplied sample swap flow (Sepolia test flow is fine)
   through `FlowMeter` + `MEVDetector`. Review the flags with the
   counterparty: flags are detection signals for protection tuning,
   nothing more.
7. **Exercise the protection policy.** Evaluate a set of counterparty
   test orders (quoted vs executed prices from their Sepolia
   environment) through `ProtectionPolicy`. Confirm: opted-in orders
   revert past the guardrail and on toxic-block flags; non-opted-in
   orders are untouched.
8. **Walk the protected-routing wiring at the counterparty layer.** If
   the engagement includes a protected RPC/stream, rehearse the
   counterparty's own routing of order flow through it on Sepolia
   (their infrastructure, their keys, their environment). This package
   observes and evaluates; it does not route or submit.
9. **Confirm the submission stop condition.** Demonstrate that
   `Bidder.submit_live()` raises `LiveBlockedError` in the drill
   configuration, and that even with the gate released in a test
   harness the module raises `NotImplementedError` — there is no
   execution path to enable.
10. **Acceptance review.** Walk the engagement's acceptance checklist
    with the counterparty, record test counts, configuration values,
    and audit results, and file the drill log with the engagement
    record.
11. **Rollback.** The module holds no on-chain state and no funds;
    rollback is removing the integration and the environment
    configuration. Nothing to unwind on-chain.

## Stop conditions

- Any request to enable live submission, operate bidding, or pursue
  capture activity is outside this package's scope and outside the
  engagement. Stop and escalate to the founder.
- Any configuration that places a treasury EOA in a signer field
  fails closed by design; do not work around the deny-list.
- Production deployment discussions happen only after a completed
  Sepolia drill and only inside the signed engagement.
