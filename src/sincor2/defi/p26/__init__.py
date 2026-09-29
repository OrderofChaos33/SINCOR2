"""P26 — Self-Improving DeFi OS (meta layer over the other 25 protocols).

P26 ranks the other 25 protocols and kills negative-ROI ticks. It consumes
REAL repo evidence only:

  - catalog.py: the 26 ProtocolSpecs (target_apr, fee_bps, risk_score,
    live_blocked, gates) — the single source of protocol truth
  - gates.py: stage-transition checks (spec_exists, implementation_present,
    unit_tests_passing, invariant_tests, fork_sim, audit_report)
  - proof_ledger.py: recorded evidence entries (test_run, invariant_test,
    fork_sim, audit_report, deploy_receipt)

Rankings are never invented: protocols with no recorded evidence rank with
confidence "unproven" and are never presented as proven. Kill decisions are
advisory records (dry-run default); P26 never executes anything live itself.

P26 is excluded from its own ranking universe (it is the ranker, not a
yield target) — the same exclusion P25 applies at ingest.
"""

PARAMS = {
    # ranker weights (documented, tunable only with re-validation)
    "w_realized": 0.50,       # weight on realized fee/ROI telemetry
    "w_catalog": 0.30,        # weight on catalog risk-adjusted target
    "w_evidence": 0.20,       # weight on proof-ledger evidence confidence
    "kill_roi_threshold": 0.0,  # realized ROI < 0 -> kill the tick
    "min_evidence_for_live_rank": 2,  # distinct ledger kinds for "proven"
}

SELF_ID = "P26_DEFI_OS"

from . import api, killswitch, proof_hooks, ranker, registry_wrap, telemetry, toa_loop


__all__ = [
    "api",
    "killswitch",
    "proof_hooks",
    "ranker",
    "registry_wrap",
    "telemetry",
    "toa_loop",
    "PARAMS",
    "SELF_ID",
]

