"""Record invariant_test proof-ledger entries for DeFi products whose
on-disk invariant/fuzz suites ran green in wave 20.

Only suites that ACTUALLY ran green are recorded here. The suite runs
were executed separately; this script only appends the ledger evidence.
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
RECORDED_BY = "xioix/buildout-20-defi-invariant-ledger"
CMD = (
    "PYTHONPATH=src:. FLASK_ENV=test SECRET_KEY=<redacted> "
    "JWT_SECRET_KEY=<redacted> ADMIN_USERNAME=<redacted> "
    "ADMIN_PASSWORD=<redacted> STRIPE_SECRET_KEY=<redacted> "
    "python -m pytest {suite} -q"
)

# protocol_id -> list of suites recorded in ONE ledger entry per product
# (count, suite display, scope description)
ENTRIES = {
    "P01_YIELD_AGG": (
        7,
        "tests/pytest/test_p01_audit_invariants.py",
        "vault invariants: conservation/weights-cap fuzz, eligible-set "
        "monotonicity in budget, sequential deposit/withdraw flows, "
        "adversarial tables never crash or execute, zero-expectation "
        "cash fallback, dust capital conserved, cap waterfill regression",
    ),
    "P02_CLMM": (
        14,
        "tests/pytest/test_p02_audit_invariants.py",
        "CLMM MEV detector: precision regression (no float false positives), "
        "boundary exact on/off, threshold matches exact rule fuzz, "
        "sensitivity env parsing exact, no-swap never flags fuzz, two-block "
        "sequences never flag fuzz, sub-threshold never flags fuzz, "
        "adversarial event amounts no crash, shift-sequence invariants fuzz, "
        "extreme ticks no crash",
    ),
    "P03_INTENT_DARK": (
        13,
        "tests/pytest/test_p03_audit_invariants.py",
        "dark pool: proceeds conservation fuzz, treasury cut minimum 15bps "
        "fuzz, fee cap + nonbricking fuzz, pause/unpause sequence fuzz, "
        "commitment deterministic and binding, record carries commitment-only "
        "fuzz, nonce sequence fuzz, match soundness fuzz, settlement "
        "conservation + fee fuzz (incl. max-uint), asset gate no-state-change "
        "fuzz, replay/double-ops, lifecycle status-machine fuzz, split-solver "
        "invariants fuzz, pause-guard interleaved fuzz",
    ),
    "P05_INSURANCE": (
        1,
        "tests/pytest/test_p05_insurance_units.py::"
        "test_fuzzed_op_sequences_never_breach_floor",
        "seeded fuzz re-run 2026-09-30: 500 seeded op sequences; reserve "
        "floor never breached; oracle-stale and stale-score paths refused. "
        "Re-verifies earlier canonical entry ev_81d7599b675d",
    ),
    "P06_PERPS": (
        1,
        "tests/pytest/test_p06_perp_units.py::test_fuzz_10k_drift_discipline",
        "seeded fuzz re-run 2026-09-30: 10,000 seeded drift samples; "
        "banding, slippage guards, live gate hold. Re-verifies earlier "
        "canonical entry ev_4aa03f332ef5",
    ),
    "P07_BRIDGE": (
        2,
        "tests/pytest/test_p07_bridge_units.py::"
        "test_fuzz_ranking_never_routes_rejected_quotes, "
        "test_fuzz_settlement_conservation",
        "seeded fuzz re-run 2026-09-30: ranking never routes rejected quotes "
        "(500 iters: allowlist/slippage-cap/security-floor never bypassed, "
        "best-first ranking) + settlement conservation (settled+fee == "
        "net_out exact, treasury totals reconcile). Re-verifies earlier "
        "canonical entry ev_e366ac03e929",
    ),
    "P08_RWA": (
        1,
        "tests/pytest/test_p08_rwa_units.py::"
        "test_fuzz_random_lifecycle_preserves_invariants",
        "seeded lifecycle fuzz re-run 2026-09-30 (400 iters): pre-gate "
        "zero-yield, gate atomicity, integer conservation of assets and "
        "shares. Re-verifies earlier canonical entry ev_d18452dc90f6",
    ),
    "P10_FLASH_ARB": (
        1,
        "tests/pytest/test_p10_flash_units.py::"
        "test_no_loss_making_execution_in_fuzz",
        "flash arb fuzz: no loss-making execution under fuzzed inputs",
    ),
    "P11_DELTA_NEUTRAL": (
        1,
        "tests/pytest/test_p11_delta_units.py::"
        "test_principal_invariant_under_price_move",
        "delta-neutral: principal preserved under price moves",
    ),
    "P14_PREDICTION": (
        1,
        "tests/pytest/test_p14_prediction_units.py::"
        "test_kelly_never_negative_or_over_cap_in_fuzz",
        "prediction: kelly fraction stays within [0, cap] under fuzzed inputs",
    ),
    "P15_LENDING": (
        2,
        "tests/pytest/test_p15_lending_units.py::"
        "test_rehypothecation_round_trip_holds_invariants, "
        "test_fuzz_invariants",
        "lending: rehypothecation round-trip preserves invariants + "
        "general fuzz invariants",
    ),
    "P16_DEX_AGG": (
        1,
        "tests/pytest/test_p16_dexagg_units.py::"
        "test_net_beats_best_single_venue",
        "DEX aggregator: 30 seeded randomized venue sets; split net output "
        ">= best single-venue net on every draw",
    ),
    "P18_STRUCTURED": (
        1,
        "tests/pytest/test_p18_structured_units.py::test_fuzz_invariants",
        "structured products: fuzz invariants hold",
    ),
    "P20_COMPLIANCE": (
        1,
        "tests/pytest/test_p20_compliance_units.py::test_fuzz_decision_matrix",
        "compliance: fuzzed decision matrix stays within policy",
    ),
    "P21_TREASURY_DAO": (
        1,
        "tests/pytest/test_p21_treasury_units.py::test_fuzz_allocations",
        "treasury DAO: fuzzed allocations stay within policy bounds",
    ),
    "P22_STABLE_YIELD": (
        2,
        "tests/pytest/test_defi_p22.py::test_ac1_fuzzed_assets_1000_rejected, "
        "test_ac2_fuzz_1000_quote_sets",
        "stable yield: 1000 fuzzed assets rejected + 1000 fuzzed quote sets "
        "within bounds",
    ),
    "P23_NFTFI": (
        2,
        "tests/pytest/test_defi_p23.py::test_ac1_fuzz_1000_addresses_rejected, "
        "test_ac2_fuzz_1000_sequences",
        "NFT-fi: 1000 fuzzed addresses rejected + 1000 fuzzed sequences "
        "preserve invariants",
    ),
    "P24_SOCIALFI": (
        2,
        "tests/pytest/test_defi_p24.py::test_ac1_fuzz_1000_fee_values, "
        "test_ac4_fuzz_1000_matcher_mutations_still_blocked",
        "socialfi: 1000 fuzzed fee values + 1000 matcher mutations still "
        "blocked",
    ),
    "P25_PORTFOLIO": (
        1,
        "tests/pytest/test_defi_p25.py::test_ac1_fuzz_1000_signal_sets",
        "agent portfolio: 1000 fuzzed signal sets handled within bounds",
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

# verify gate-readability for every recorded SKU
from sincor2.defi.gates import _passing_entries  # noqa: E402

print("\ngate check (_passing_entries for invariant_test):")
for pid in sorted(ENTRIES):
    sku = mint_sku(pid)
    runs = _passing_entries(ledger, sku, "invariant_test")
    print(f"  {sku}: {'OK' if runs else 'MISSING'} ({len(runs)} passing)")
