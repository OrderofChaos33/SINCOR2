"""Record invariant_test proof-ledger entries for DeFi products whose
new wave-21 seeded fuzz/invariant suites ran green on 2026-09-30.

Only suites that ACTUALLY ran green are recorded here. The suite runs
were executed separately (53/53 passed); this script only appends the
ledger evidence.

Honest findings discovered by the suites are noted in the scope text
rather than hidden: the suites pass on corrected invariants, and the
findings are reported for follow-up instead of silently absorbed.
"""

from __future__ import annotations

import subprocess
import sys

sys.path.insert(0, "src")

from sincor2.defi.proof_ledger import ProofLedger  # noqa: E402
from sincor2.defi.products import mint_sku  # noqa: E402

COMMIT = (
    subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    .stdout.strip()
)
RECORDED_BY = "xioix/buildout-21-defi-fuzz-suites"
CMD = (
    "PYTHONPATH=src:. FLASK_ENV=test SECRET_KEY=<redacted> "
    "JWT_SECRET_KEY=<redacted> ADMIN_USERNAME=<redacted> "
    "ADMIN_PASSWORD=<redacted> STRIPE_SECRET_KEY=<redacted> "
    "python -m pytest {suite} -q"
)

# protocol_id -> (count, suite, scope description)
ENTRIES = {
    "P04_MEV": (
        12,
        "tests/pytest/test_p04_mev_fuzz_invariants.py",
        "MEV protection: FlowMeter idempotency/accounting, gas budget, "
        "threshold conjunction, detector signatures/noise, treasury signer "
        "deny-list, bidder shading/float/live gate, capture reconciliation, "
        "treasury routing conservation, protection policy, access controls. "
        "Seeded RNG 0xA00404.",
    ),
    "P09_DAO_GOV": (
        6,
        "tests/pytest/test_p09_dao_fuzz_invariants.py",
        "DAO governance: emission validity/ranking, linear vote-market "
        "pricing, bribe-plan net decomposition/rejections, epoch "
        "conservation/fee, quorum/timelock/snapshot behavior, fail-closed "
        "broadcast. Seeded RNG 0xA00909.",
    ),
    "P12_TWAMM": (
        6,
        "tests/pytest/test_p12_twamm_fuzz_invariants.py",
        "TWAMM: constant-product integer math, schedule conservation / "
        "determinism / impact cap / fees, auto-extension, gate failures, "
        "lookahead, fail-closed venue submission. FINDING (reported): module "
        "doc/property 'total_output >= dump_output' is not always true under "
        "per-slice integer flooring (observed sliced 630573 vs dump 630585, "
        "deficit 12 cents); tested invariant is dump_output - total_output "
        "<= n_slices cents. No source changed. Seeded RNG 0xA01212.",
    ),
    "P13_AVS": (
        5,
        "tests/pytest/test_p13_avs_fuzz_invariants.py",
        "AVS tranching: oracle range/freshness gates, allocation floor / "
        "junior cap / coverage / conservation, slash waterfall (junior-first, "
        "cap-strict, conservation), yield waterfall (senior-first, exact fee, "
        "overdraft refused), fail-closed live restaking. Seeded RNG 0xA01313.",
    ),
    "P17_OPTIONS": (
        10,
        "tests/pytest/test_p17_options_fuzz_invariants.py",
        "options: vol clamp bounds incl NaN/inf, BS premium non-negativity "
        "within float dust + put-call parity, tenor band, quote rails, "
        "acceptance 2% boundary exact, fee exact 15 bps, feed staleness, "
        "covered-only invariant under fuzzed writes, lifecycle sequences "
        "(once-only settle, exact ITM payoff to the wei), pause halts writes "
        "not withdrawals. FINDING (reported): float pricer can return as low "
        "as -5.9e-12 on deep-OTM draws (400k-draw bound); fail-safe because "
        "quote_premium rejects below rails/dust. Seeded RNG 0xA01717.",
    ),
    "P19_CREDIT": (
        6,
        "tests/pytest/test_p19_credit_fuzz_invariants.py",
        "credit underwriting: score bounds/determinism, mixer clamp, LTV "
        "monotonicity + invalid-table rejection, attestation freshness / "
        "replay / floor / attestor auth, no-unsecured-book line invariants, "
        "exact health-factor and circuit thresholds, concentration caps incl "
        "exact 10% boundary, exact fee accounting. FINDINGS (reported): (a) "
        "documented score ceiling 850 unreachable — weight budget caps at 800 "
        "with zero penalties; top LTV band (750+) still reachable; (b) "
        "validate_ltv_table never range-checks the first row's LTV "
        "(((750,0),) and ((750,99999),) pass). On-disk table valid; "
        "max_ltv_bps unaffected. Seeded RNG 0xA01919.",
    ),
    "P26_DEFI_OS": (
        8,
        "tests/pytest/test_p26_defios_fuzz_invariants.py",
        "DeFi OS meta-layer: telemetry ingest/latest exactness, killswitch "
        "advisory-only fuzz (negative ROI always KILL_TICK, never executed, "
        "self/unknown fail-closed), ranker score decomposition "
        "0.5*realized+0.3*catalog+0.2*evidence exact + honest confidence "
        "labels (no-evidence protocols 'unproven', never 'proven'), feedback "
        "weights purity, proof hooks + serializable decision package, posture "
        "partition of the 25-protocol universe. Seeded RNG 0xA02626.",
    ),
}

ledger = ProofLedger()
for pid in sorted(ENTRIES):
    passed, suite, scope = ENTRIES[pid]
    sku = mint_sku(pid)
    entry = ledger.append(
        sku=sku,
        kind="invariant_test",
        details={
            "command": CMD.format(suite=suite),
            "suite": suite,
            "scope": scope,
            "passed": passed,
            "failed": 0,
        },
        commit=COMMIT,
        recorded_by=RECORDED_BY,
    )
    print(f"{sku}: {entry['entry_id']} (passed={passed})")

from sincor2.defi.gates import _passing_entries  # noqa: E402

print("\ngate check (_passing_entries for invariant_test):")
for pid in sorted(ENTRIES):
    sku = mint_sku(pid)
    runs = _passing_entries(ledger, sku, "invariant_test")
    print(f"  {sku}: {'OK' if runs else 'MISSING'} ({len(runs)} passing)")

print("\nledger JSON validation:")
import json  # noqa: E402

json.load(open(ledger.path))
print(f"  valid JSON ({ledger.path})")
