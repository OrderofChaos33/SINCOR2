# P24 — MEV Protection & Capture (suite P04)

MEV protection module extracted from the SINCOR2 platform for
counterparty engagement packaging. Per-order protection evaluation,
swap-flow metering, and sandwich / backrun detection over observed
flow, with a fail-closed signer deny-list and dry-run-only submission
behaviour — as a Python reference build (standard library only).

Commercial scope is **protection / integration work only**. This
package makes no MEV capture revenue claims: SINCOR's own backrun
lane was separately measured and closed, and nothing here is offered
as a capture capability.

State: **EXTERNAL** — the module is not separately deployed today; a
buyer/counterparty and a signed engagement are required before any
deployment.

## Layout

- `src/p24_mev_protection/mev_capture.py` — the module, extracted
  verbatim from the platform source (see PACKAGING.md).
- `src/p24_mev_protection/__init__.py` — public API re-exports.
- `tests/` — the module's platform test suites, adapted to the
  package, plus added protection/submission/configuration coverage
  and a packaging smoke test.
- `DEPLOY.md` — configuration surface, dependencies, Sepolia drill.
- `deploy/env.example` — the complete environment-variable surface.
- `ENGAGEMENT.md` — engagement scope template and priced terms.
- `PACKAGING.md` — extraction record, test counts, founder gates.

## Run the tests

```bash
cd packages/defi/p24-mev-protection
python -m pytest   # 68 tests at packaging time
```
