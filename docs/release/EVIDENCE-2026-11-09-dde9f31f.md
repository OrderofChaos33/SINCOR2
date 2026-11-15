# RELEASE EVIDENCE — dde9f31f (LOCAL RUNS ONLY)

- **Release SHA:** `dde9f31f8e7954f82a491527846d47206f7ab82a`
- **Evidence branch:** `xioix/release-evidence-dde9f31f` (content = pristine main at the SHA above; this package adds this document only — no production code or existing test was changed)
- **Evidence date:** Sunday 2026-11-08, 19:47–19:59 CST (America/Chicago)
- **Purpose:** Genspark launch-readiness gate item — "release gates manual-only, no PR-check runs as evidence." This package is the honest local substitute, produced under the founder's "Fix everything" order of 2026-11-08.

> **PLAIN STATEMENT:** GitHub CI evidence does **not** exist for this SHA. No PR-check run, workflow run, or hosted CI result is claimed anywhere in this document. Every number below comes from a local `pytest` run executed on the machine described here, on the date above, and was re-measured for this package (not copied from earlier reports). Earlier coordinator runs on the same SHA (through 2026-11-08 18:00 CST) are cited only as the historical baseline each suite is compared against.

## Environment

- **OS:** Linux (Ubuntu kernel 7.0.0-43-generic, x86_64)
- **Python:** 3.12.3, venv `~/.venvs/sincor2` (note: production runs a Python 3.11 image; these are local test-environment results, not production-runtime results)
- **pytest:** 9.1.1
- **Working tree:** isolated git worktree at the exact SHA, clean before and after the runs
- **Test environment (CI-mirror, values are test dummies, never real credentials):**
  `PYTHONPATH=src:.`, `FLASK_ENV=test`, `PLATFORM_OFAC_STRICT=false`, `SECRET_KEY=<32-char test dummy>`, `JWT_SECRET_KEY=<32-char test dummy>`, `ADMIN_USERNAME=admin`, `ADMIN_PASSWORD=<test dummy>`, `STRIPE_SECRET_KEY=sk_test_<dummy>`
- **TMPDIR note (material — see Discrepancy 1):** the first new-wave run used a TMPDIR inside the worktree; the confirmation runs used `TMPDIR=/tmp/evidence-tmp-c` (outside the tree). Both results are recorded.

## Results

All commands were run from the worktree root as:
`~/.venvs/sincor2/bin/python -m pytest <files> -q` with the environment above.

| # | Suite | Files | Started (CST) | Passed | Failed | Duration | Verdict |
|---|-------|-------|---------------|--------|--------|----------|---------|
| a | Go-live set | `test_escalation_hardening.py`, `test_kya_telemetry.py`, `test_kya_telemetry_wiring.py`, `test_phase2_signing_merkle.py`, `test_sanctions_escalation_wire.py`, `test_workflow_state.py`, `test_workflow_state_concurrency.py`, `test_checkpointed_marketplace_persistence.py` | 2026-11-08 19:47:35 | **124** | **0** | 276.21s | PASS — matches historical 124/124 |
| b | Wren set | `test_wren_pr1_intake_admin.py`, `test_wren_pr2_buyer_recovery.py`, `test_wren_pr3_legacy_crypto_retired.py`, `test_wren_pr4_admin_hardening.py`, `test_wren_pr5_retry_bind.py`, `test_wren_pr7_griefing_logs.py`, `test_wren_pr8_adversarial_fixes.py`, `test_wren_pr8_polish.py` | 2026-11-08 19:52:24 | **96** | **0** | 184.00s | PASS — matches historical 96/96 |
| c1 | New-wave set (first run, TMPDIR inside worktree) | `test_adopt24_t1_skills.py`, `test_adopt24_t2_skills.py`, `test_b44_claim_ratelimit_csp.py`, `test_b44_docs_serving.py`, `test_b44_new_caller_quote.py`, `test_b44_site_surface.py`, `test_w136_gate_template.py`, `test_w136_genesis_claim_fix.py` | 2026-11-08 19:55:47 | 89 | 3 | 76.75s | 2 known-stale gate failures + 1 TMPDIR artifact (Discrepancy 1) |
| c2 | New-wave set (confirmation re-run, TMPDIR outside tree) | same 8 files as c1 | 2026-11-08 19:58:02 | **90** | **2** | 17.61s | Matches historical post-boundary result (90/2); both failures are the known-stale gate pair |
| d | 4-file verification set | `test_card_scrub_discovery_prep.py`, `test_skill_registry_integrity.py`, `test_wallet_sanctions_a2a_skill.py`, `test_obs_route_table.py` | 2026-11-08 19:58:27 | **31** | **1** | 12.78s | Sole failure is the known-stale card-count test; route-table test passed in-session |
| e | #600 intake_route regression (single node) | `test_intake_route.py::test_fulfill_pack_agent_after_intake_keeps_token_and_wallet` | 2026-11-08 19:58:46 | **1** | **0** | 3.30s | PASS — W-123 gate fix holds |

Suite compositions were taken from the coordinator run notes under `~/workspace/goals/sincor-complete-build-out/hidden_files/` (runs of 2026-11-06 → 2026-11-08), which name the same file sets and historical counts (124 / 96 / 92 pre-boundary / 31+1 / 1).

## KNOWN STALE — every failing test, with its explanation

1. **`test_w136_gate_template.py::test_gate_renders_claim_form_and_pending_handler`** — STALE (date-expired).
2. **`test_b44_site_surface.py::test_gate_claim_form_has_no_password_field`** — STALE (date-expired).
   Both GET `/` and assert `id="gate"`. At 2026-11-09 00:00 UTC the launch-date logic in `pages.py` (`date() >= _launch_date()`, default 2026-11-09) flipped, so `/` correctly serves the home page instead of the genesis gate — intended launch-day behavior, not a code regression. This set scored 92/92 before the boundary and 90/2 after it on identical code (verified 2026-11-08 14:00 → 18:00 CST). Fix pattern (separate, founder-authorized work): pin a future `SINCOR_LAUNCH_DATE` in the fixtures. **Not edited here.**
3. **`test_card_scrub_discovery_prep.py::test_card_still_has_18_skills_and_market_forecast_intact`** — STALE (superseded count). Asserts the agent card still has 18 skills (`assert 42 == 18`); the catalog is 42 since PR #630 (Adopt-24) merged. **Not edited here.**

## Discrepancies vs historical numbers

1. **New-wave first run: 89 passed / 3 failed vs historical 90/2.** The third failure, `test_b44_docs_serving.py::test_missing_doc_still_404s_when_no_root_has_it` (`assert 200 == 404`), is a **test-environment artifact of this package's first run, not a code change or regression**: the docs resolver (`src/sincor2/mvp_blueprints/discovery.py::_repo_doc_candidates`) walks every ancestor directory of the module path looking for a `docs/` tree. With TMPDIR inside the worktree, the test's simulated bare root lives under the worktree, so the ancestor walk reaches the worktree root and finds the real `docs/TRUST_STACK.md`. Proof, both recorded above: (i) the single test re-run with TMPDIR outside the tree passes (1 passed in 0.65s); (ii) the full set re-run with TMPDIR outside the tree reproduces the exact historical 90 passed / 2 failed. No code or test was changed to obtain the clean result — only the temp directory location.
2. **Durations are far longer than historical** (e.g. go-live 276.21s vs ~8–9s historically). The runs shared one VM (1.5 shared CPUs) with several parallel builder agents working at the same time. Counts are unaffected; durations in this document are the honestly measured ones under that load and should not be quoted as suite performance figures.

No other discrepancies: suites (a), (b), (d), and (e) reproduced their historical counts exactly.

## LIMITATIONS

- **Local runs only — this is not GitHub CI evidence.** No hosted check run exists for this SHA; this package does not create one and does not claim one.
- **Test environment ≠ production runtime:** Python 3.12.3 local venv vs the production Python 3.11 image; `PLATFORM_OFAC_STRICT=false` mirrors CI, so strict-mode sanctions behavior is not evidenced here.
- **OFAC vendored snapshot is stale:** `src/sincor2/defi/data/p20_ofac_sdn.json` on this SHA carries publication date **10/28/2026**, `fetched_at` 2026-11-04, 19,484 entries — beyond the 48-hour freshness window (oracle verdict: STALE). The refreshed snapshot exists only on an unmerged local branch; nothing in this package changes that.
- **Scope:** only the five suite groups above were run. This is not a full-suite run, not a penetration test, not a live-site probe, and not a paid-purchase end-to-end test.
- **No funds were moved**, no deploys were made, no production system was touched in producing this evidence.

## Sign-off

Founder sign-off: ______________________________  Date: ______________
