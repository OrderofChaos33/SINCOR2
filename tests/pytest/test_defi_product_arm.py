"""Tests for the SINCOR Speculative DeFi product arm.

Self-contained: isolated data dir per test via SINCOR_DEFI_ARM_DATA_DIR.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sincor2.defi import products
from src.sincor2.defi.catalog import PROTOCOLS, PROTOCOL_BY_ID
from src.sincor2.defi.compliance_score import score_product
from src.sincor2.defi.gates import STAGE_MAP, STAGES, evaluate
from src.sincor2.defi.pricing import STATUS_DRAFT, activate_pricing, price_for
from src.sincor2.defi.proof_ledger import (
    KIND_AUDIT_REPORT,
    KIND_FORK_SIM,
    KIND_INVARIANT_TEST,
    KIND_TEST_RUN,
    ProofLedger,
)

SKU_RE = re.compile(r"^SINCOR-DEFI-P\d{2}-[A-Z0-9]+$")


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    d = tmp_path / "arm_data"
    monkeypatch.setenv("SINCOR_DEFI_ARM_DATA_DIR", str(d))
    return d


@pytest.fixture()
def ledger(isolated):
    return ProofLedger()


def _at_stage(sku, stage, **over):
    p = products.get_product(sku)
    assert p is not None
    p["stage"] = stage
    p.update(over)
    return p


# -- registry ------------------------------------------------------------
def test_all_26_registered_no_drift(isolated):
    reg = products.build_registry()
    assert len(reg) == 26
    assert {p["protocol_id"] for p in reg} == {p.protocol_id for p in PROTOCOLS}
    assert all(SKU_RE.match(p["sku"]) for p in reg)
    assert len({p["sku"] for p in reg}) == 26
    assert all(p["division"] == "speculative-defi" for p in reg)


def test_initial_stages(isolated):
    reg = {p["protocol_id"]: p for p in products.build_registry()}
    # P01/P04/P05/P06 have real strategy math in the repo -> start at build
    for pid in ("P01_YIELD_AGG", "P04_MEV", "P05_INSURANCE", "P06_PERPS"):
        assert reg[pid]["stage"] == "build", pid
    for pid, p in reg.items():
        if pid not in ("P01_YIELD_AGG", "P04_MEV", "P05_INSURANCE", "P06_PERPS"):
            assert p["stage"] == "spec", pid


def test_pricing_and_marketing_defaults(isolated):
    for p in products.build_registry():
        assert p["pricing_status"] == STATUS_DRAFT
        assert p["marketing_status"] == "not-started"
        assert price_for(p["protocol_id"])["base_fee_bps"] == PROTOCOL_BY_ID[p["protocol_id"]].fee_bps


def test_stage_map_covers_auction_phases(isolated):
    phases = set()
    adir = Path.home() / "workspace" / "sincor2-auction-tasks"
    if not adir.exists():
        pytest.skip("auction tasks dir absent")
    for f in adir.glob("p*.json"):
        for t in json.loads(f.read_text()):
            phases.add(t["phase"])
    assert phases, "no auction tasks found"
    assert phases <= set(STAGE_MAP), f"unmapped phases: {phases - set(STAGE_MAP)}"
    assert set(STAGE_MAP.values()) <= set(STAGES)


# -- gates: happy paths ---------------------------------------------------
def test_spec_to_build_happy(isolated, ledger):
    p = _at_stage("SINCOR-DEFI-P02-CLMM", "spec")
    res = evaluate(p, "build", ledger, products.REPO_ROOT)
    assert res.ok, [r.__dict__ for r in res.reasons]


def test_build_to_test_happy(isolated, ledger):
    sku = "SINCOR-DEFI-P01-VAULT"
    ledger.append(sku, KIND_TEST_RUN, {"suite": "test_yield_aggregator", "passed": 12, "failed": 0})
    p = _at_stage(sku, "build")
    res = evaluate(p, "test", ledger, products.REPO_ROOT)
    assert res.ok, [r.__dict__ for r in res.reasons]


@pytest.mark.parametrize("sku,suite,passed", [
    ("SINCOR-DEFI-P04-MEV", "tests/pytest/test_p04_mev_units.py", 33),
    ("SINCOR-DEFI-P05-MUTUAL", "tests/pytest/test_p05_insurance_units.py", 26),
    ("SINCOR-DEFI-P06-PERPS", "tests/pytest/test_p06_perp_units.py", 27),
])
def test_build_to_test_happy_p04_p05_p06(isolated, ledger, sku, suite, passed):
    """P04/P05/P06 have real implementations + passing suites: build->test ok."""
    ledger.append(sku, KIND_TEST_RUN,
                  {"suite": suite, "passed": passed, "failed": 0})
    p = _at_stage(sku, "build")
    res = evaluate(p, "test", ledger, products.REPO_ROOT)
    assert res.ok, [r.__dict__ for r in res.reasons]


def test_test_to_audit_happy(isolated, ledger):
    sku = "SINCOR-DEFI-P01-VAULT"
    ledger.append(sku, KIND_INVARIANT_TEST, {"suite": "invariant", "passed": 5, "failed": 0})
    ledger.append(sku, KIND_FORK_SIM, {"suite": "fork", "passed": 3, "failed": 0})
    p = _at_stage(sku, "test")
    res = evaluate(p, "audit", ledger, products.REPO_ROOT)
    assert res.ok, [r.__dict__ for r in res.reasons]


def test_audit_to_product_happy(isolated, ledger):
    sku = "SINCOR-DEFI-P01-VAULT"  # not live-blocked
    ledger.append(sku, KIND_AUDIT_REPORT, {"auditor": "test", "open_critical": 0})
    p = _at_stage(sku, "audit")
    res = evaluate(p, "product", ledger, products.REPO_ROOT)
    assert res.ok, [r.__dict__ for r in res.reasons]


def test_product_to_catalog_happy_path_reaches_compliance_check(isolated, ledger):
    # P12's spec is explicitly oracle-less; with full synthetic evidence the
    # only bar that can still fail is the compliance threshold itself.
    sku = "SINCOR-DEFI-P12-TWAMM"
    ledger.append(sku, KIND_TEST_RUN, {"suite": "t", "passed": 10, "failed": 0})
    ledger.append(sku, KIND_AUDIT_REPORT, {"auditor": "t", "open_critical": 0})
    p = _at_stage(sku, "product", pricing_status="live", marketing_status="approved")
    res = evaluate(p, "catalog", ledger, products.REPO_ROOT)
    score = score_product(p, ledger=ledger, root=products.REPO_ROOT)["score"]
    assert score >= 70, f"expected synthetic evidence to clear 70, got {score}"
    assert res.ok, [r.__dict__ for r in res.reasons]


# -- gates: refusals --------------------------------------------------------
def test_no_stage_skipping(isolated, ledger):
    p = _at_stage("SINCOR-DEFI-P02-CLMM", "spec")
    res = evaluate(p, "test", ledger, products.REPO_ROOT)
    assert not res.ok
    assert any(r.check == "no_skip" for r in res.reasons)


def test_build_to_test_refuses_without_implementation(isolated, ledger):
    # NOTE: uses P07_BRIDGE as the no-implementation example. P01-P06 and
    # P08/P10/P14 now ship reference implementations, so they no longer
    # exercise the "missing implementation" refusal path. Convention:
    # lowest-numbered product without an entry in gates.IMPLEMENTATIONS.
    p = _at_stage("SINCOR-DEFI-P07-BRIDGE", "build")
    res = evaluate(p, "test", ledger, products.REPO_ROOT)
    assert not res.ok
    failed = {r.check for r in res.reasons if not r.ok}
    assert "implementation_present" in failed
    assert all(r.evidence for r in res.reasons)  # machine-readable


def test_new_implementations_reach_test_gate(isolated, ledger):
    # P08/P10/P14 reference builds land with passing test-run evidence:
    # build -> test must now pass for all three.
    for sku in ("SINCOR-DEFI-P08-RWA", "SINCOR-DEFI-P10-FLASHARB",
                "SINCOR-DEFI-P14-PREDICT"):
        ledger.append(sku, KIND_TEST_RUN,
                      {"suite": "t", "passed": 20, "failed": 0})
        p = _at_stage(sku, "build")
        res = evaluate(p, "test", ledger, products.REPO_ROOT)
        assert res.ok, (sku, [r.__dict__ for r in res.reasons])


def test_live_blocked_cannot_reach_product(isolated, ledger):
    blocked = [p for p in PROTOCOLS if p.live_blocked]
    assert len(blocked) >= 15
    sku = products.mint_sku(blocked[0].protocol_id)
    ledger.append(sku, KIND_AUDIT_REPORT, {"auditor": "t", "open_critical": 0})
    p = _at_stage(sku, "audit")
    res = evaluate(p, "product", ledger, products.REPO_ROOT)
    assert not res.ok
    assert any(r.check == "not_live_blocked" and not r.ok for r in res.reasons)


def test_audit_with_open_criticals_refuses(isolated, ledger):
    sku = "SINCOR-DEFI-P01-VAULT"
    ledger.append(sku, KIND_AUDIT_REPORT, {"auditor": "t", "open_critical": 2})
    p = _at_stage(sku, "audit")
    res = evaluate(p, "product", ledger, products.REPO_ROOT)
    assert not res.ok
    assert any(r.check == "audit_report" and not r.ok for r in res.reasons)


def test_unknown_sku_promote_refuses(isolated, ledger):
    out = products.promote("SINCOR-DEFI-P99-NOPE", "build", ledger=ledger,
                           root=products.REPO_ROOT)
    assert not out["ok"]


def test_promote_persists_and_enforces_order(isolated, ledger):
    out = products.promote("SINCOR-DEFI-P02-CLMM", "build", ledger=ledger,
                           root=products.REPO_ROOT)
    assert out["ok"], out["reasons"]
    assert products.get_product("SINCOR-DEFI-P02-CLMM")["stage"] == "build"
    out2 = products.promote("SINCOR-DEFI-P02-CLMM", "audit", ledger=ledger,
                            root=products.REPO_ROOT)
    assert not out2["ok"]  # build -> audit skips test


# -- compliance --------------------------------------------------------------
def test_scores_bounded_and_low_under_testing(isolated, ledger):
    for p in products.build_registry():
        s = score_product(p, ledger=ledger, root=products.REPO_ROOT)
        assert 0 <= s["score"] <= 100
        assert set(s["dimensions"]) == {"audit_evidence", "test_evidence", "oracle_risk",
                                        "custody", "fail_closed", "regulatory"}
    # spot: with zero evidence the arm scores low — honest under-testing state
    p01 = score_product(products.get_product("SINCOR-DEFI-P01-VAULT"),
                        ledger=ledger, root=products.REPO_ROOT)
    assert p01["score"] < 70
    assert p01["dimensions"]["audit_evidence"]["score"] == 0
    assert p01["dimensions"]["audit_evidence"]["evidence"] == "unverified"


def test_missing_evidence_scores_zero_not_assumed(isolated, ledger):
    p = products.get_product("SINCOR-DEFI-P09-GOV")
    dims = score_product(p, ledger=ledger, root=products.REPO_ROOT)["dimensions"]
    assert dims["test_evidence"] == {"score": 0, "max": 20, "evidence": "unverified"}


def test_fail_closed_dimension_detects_p20(isolated, ledger):
    p = products.get_product("SINCOR-DEFI-P20-COMPLY")
    dims = score_product(p, ledger=ledger, root=products.REPO_ROOT)["dimensions"]
    assert dims["fail_closed"]["score"] == 10
    assert dims["fail_closed"]["evidence"] != "unverified"


def test_audit_evidence_moves_score(isolated, ledger):
    sku = "SINCOR-DEFI-P01-VAULT"
    p = products.get_product(sku)
    before = score_product(p, ledger=ledger, root=products.REPO_ROOT)["score"]
    ledger.append(sku, KIND_AUDIT_REPORT, {"auditor": "t", "open_critical": 0})
    ledger.append(sku, KIND_TEST_RUN, {"suite": "t", "passed": 8, "failed": 0})
    after = score_product(p, ledger=ledger, root=products.REPO_ROOT)["score"]
    assert after > before


# -- proof ledger ---------------------------------------------------------------
def test_ledger_append_read_roundtrip(isolated):
    lg = ProofLedger()
    e = lg.append("SINCOR-DEFI-P01-VAULT", KIND_TEST_RUN,
                  {"suite": "t", "passed": 3, "failed": 0}, commit="abc123")
    assert e["entry_id"].startswith("ev_")
    assert e["commit"] == "abc123"
    assert lg.count(sku="SINCOR-DEFI-P01-VAULT") == 1
    assert lg.read(kind=KIND_TEST_RUN)[0]["entry_id"] == e["entry_id"]
    # append-only: no update/delete API
    assert not hasattr(lg, "update") and not hasattr(lg, "delete")
    # survives reload
    lg2 = ProofLedger(lg.path)
    assert lg2.count() == 1


def test_ledger_rejects_unknown_kind(isolated):
    with pytest.raises(ValueError):
        ProofLedger().append("SINCOR-DEFI-P01-VAULT", "bogus", {})


# -- pricing ----------------------------------------------------------------------
def test_activate_pricing_refuses_before_product(isolated):
    p = products.get_product("SINCOR-DEFI-P01-VAULT")
    out = activate_pricing(p)
    assert not out["ok"]
    assert p["pricing_status"] == STATUS_DRAFT


def test_marketing_approval_refuses_before_product(isolated):
    out = products.approve_marketing("SINCOR-DEFI-P01-VAULT")
    assert not out["ok"]


# -- CLI ----------------------------------------------------------------------------
def test_cli_status_runs(isolated, capsys):
    assert products.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "SINCOR-DEFI-P01-VAULT" in out
    assert "UNDER-TESTING" in out


def test_cli_gates_blocked(isolated, capsys):
    # P01 at build with empty ledger: build->test gate must refuse
    assert products.main(["gates", "SINCOR-DEFI-P01-VAULT"]) == 1
    out = capsys.readouterr().out
    assert "BLOCKED" in out
    assert "implementation_present" in out or "unit_tests_passing" in out
