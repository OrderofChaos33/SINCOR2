# Audit-prep residual notes — P01–P03 (2026-09-28)

Adversarial self-review findings NOT fixed in this track. Each is a question
an external auditor is likely to raise; recorded here so none is lost.

## Fixed during this track (for the audit record)

1. **P02 JIT threshold float64 misclassification** — `removed >= added * 0.95`
   false-flagged at wei scale (concrete 2**199-scale false positive recorded
   in `test_p02_audit_invariants.py::test_precision_regression_float_false_positive`).
   Fixed: integer bps + exact cross-multiplication.
2. **P01 concentration-cap bypass** — single-pass overflow redistribution pushed
   the room-holding strategy to 0.60 on a 2-strategy table. Fixed: water-fill.
   Precedence documented: with < 3 eligible strategies conservation wins.
3. **P01 cash fallback picked the first CASH-kind row regardless of risk/enabled.**
   Fixed: safest enabled cash. Default-universe behavior unchanged.
4. **P01 zero-expectation tables emitted zero-weight allocations** summing to 0
   (incoherent plan; plus a `KeyError` in the first version of the fix).
   Fixed: fail safe to safest enabled cash, else empty plan.

## Residual (open) attack ideas / auditor questions

### R1. P02: `CLMM_JIT_SENSITIVITY_BPS` has no range validation
`_jit_sensitivity_bps()` does `int(os.getenv(...))` with no bounds check.
`bps <= 0` makes the detector flag *every* add/remove pair (unjust surcharge
on honest LPs); `bps > 10000` (or a huge value) silently disables detection
entirely. A typo in the deploy env — or env injection in a compromised
pipeline — degrades the defense with no warning. The legacy float path had
the same flaw, so this is pre-existing, but the audit-prep touched exactly
this code. Recommendation: fail loud at import when outside `[1, 10000]`.

### R2. P02: block-scoped detection is structurally blind to cross-block JIT
`add`+`swap` in block N and `remove` in block N+1 never flags, by design
("legitimate rebalancing"). A JITter who can influence their own inclusion
timing (private mempool, validator relationships, short block times) can
straddle the boundary deliberately. The threat model implicitly assumes
JITters cannot control block boundaries — that assumption needs an explicit
auditor decision per chain. Separately: `JITFlag`s are purely advisory in
this reference build — nothing in the module *acts* on a flag (no surcharge
application, no exclusion). The enforcement half of the defense lives
outside the audited surface.

### R3. P01: `YieldAggregator(strategies=[])` silently becomes the default universe
The constructor uses `strategies or DEFAULT_STRATEGIES`, so an explicitly
empty table is falsy and falls back to defaults. An operator passing `[]`
expecting "allocate nothing" gets the full default allocation instead —
a fail-open config wart. Recommendation: `if strategies is None` check.
Documented in `test_p01_audit_invariants.py`; not changed (constructor
semantics, needs product decision).

### R4. P01: cap is a module constant, and weights are 6dp-rounded on output
`MAX_SINGLE_STRATEGY_PCT` cannot be set per-plan; a high-risk regime that
wants a tighter cap requires a code change. Additionally, `weight=round(w, 6)`
on output means the *reported* weights can sum to `1 ± n*5e-7` — immaterial
for allocation, but any downstream consumer that re-normalizes or asserts
exact sums should know.

### R5. P03: `settle()` raises bare `KeyError` on unknown intent ids
`intent = intents[iid]` — a malformed batch (intent id not in the dict)
crashes with `KeyError` instead of `DarkPoolError`. No fund risk (the crash
happens before any state change), but the integration path should treat
`verify_batch` as a mandatory gate before `settle`, and the error type
should be domain-typed. Note the engine (`src/sincor2/defi/engine.py`) does
not wire the dark-pool settle path at all yet — settlement is exercised only
through the reference lifecycle tests.

### R6. P02/P03: pause is role-gated but role *grant* is out of scope
Pause/unpause sequences are tested, but who holds the pauser role and how
roles are granted/revoked is not modeled in these modules. An auditor will
want the role-admin story (multisig? timelock?) before the pause counts as
a real defense.

## Deliberately out of scope for this track
- `tests/test_yield_aggregator.py`: 3 failures pre-exist on the pristine
  tree (stale expectations against an older module version); failing set is
  byte-identical before/after this track's changes. Not touched.
- Fork simulation: `FORK_SIM_NOTES.md` records pinned vs unpinned addresses;
  no fork run is claimed.
