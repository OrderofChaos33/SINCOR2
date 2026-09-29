"""P26 acceptance tests: 25-protocol universe, telemetry, ranker honesty,
kill-switch, TOA feedback bounds, proof hooks, gate-stage evidence, read API.

Maps to the numbered acceptance criteria in
~/workspace/sincor2-auction-tasks/specs/p26-defios.md.
Self-contained: no network, no fork (fork runs are a documented gap).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sincor2.defi.p26 import (
    PARAMS, SELF_ID, api, killswitch, proof_hooks, ranker, registry_wrap,
    telemetry, toa_loop,
)
from src.sincor2.defi.p26.api import DefiOSApi
from src.sincor2.defi.p26.killswitch import KillDecision, KillSwitch, KillSwitchError, Tick
from src.sincor2.defi.p26.ranker import Ranker, gate_stage_evidence
from src.sincor2.defi.p26.telemetry import Telemetry
from src.sincor2.defi.p26.toa_loop import feedback
from src.sincor2.defi.proof_ledger import KIND_TEST_RUN, ProofLedger
from src.sincor2.defi.catalog import PROTOCOL_BY_ID

P01 = "P01_YIELD_AGG"
P02 = "P02_CLMM"


@pytest.fixture()
def ledger(tmp_path):
    return ProofLedger(path=str(tmp_path / "ledger.json"))


@pytest.fixture()
def tel():
    return Telemetry()


def _ranker(ledger, tel, root=ROOT):
    return Ranker(ledger, tel, root=str(root))


# -- AC1: 25-protocol universe, P26 never ranked ---------------------------------------
def test_ac1_universe_is_25_and_excludes_p26():
    universe = registry_wrap.ranked_universe()
    assert len(universe) == 25
    assert SELF_ID == "P26_DEFI_OS"
    assert SELF_ID in PROTOCOL_BY_ID  # the catalog has 26 entries...
    assert all(p.protocol_id != SELF_ID for p in universe)  # ...25 are ranked


def test_ac1_live_blocked_posture_matches_catalog():
    blocked = registry_wrap.live_blocked_set()
    expected = sorted(pid for pid, p in PROTOCOL_BY_ID.items()
                      if p.live_blocked and pid != SELF_ID)
    assert blocked == expected
    assert blocked  # the fixture has live-blocked protocols


# -- AC2: ranker never invents numbers ------------------------------------------------------
def test_ac2_unproven_protocols_carry_no_invented_metrics(ledger, tel):
    rows = _ranker(ledger, tel).rank()
    assert len(rows) == 25
    for row in rows:
        assert row.confidence == "unproven"
        assert row.evidence_kinds == []
        assert row.has_telemetry is False
        assert row.components["realized"] == 0.0
        assert row.components["evidence_confidence"] == 0.0
        # the catalog component is DERIVED from the catalog, never invented:
        # target_apr * (1 - risk_score), exactly (P20_COMPLIANCE honestly has
        # target_apr 0.0, so its component is 0.0 — that is labeled, not made up)
        spec = PROTOCOL_BY_ID[row.protocol_id]
        expected = max(spec.target_apr, 0.0) * (1.0 - spec.risk_score)
        assert row.components["catalog_risk_adjusted"] == pytest.approx(expected)
        assert row.detail["realized_source"] == "none"


def test_ac2_ledger_test_run_moves_confidence_to_evidenced(ledger, tel):
    from src.sincor2.defi.products import mint_sku
    sku = mint_sku(P01)
    ledger.append(sku, KIND_TEST_RUN,
                  {"command": "pytest", "passed": 40, "failed": 0},
                  commit="abc123")
    rows = _ranker(ledger, tel).rank()
    p01 = next(r for r in rows if r.protocol_id == P01)
    assert p01.confidence == "evidenced"
    assert p01.evidence_kinds == [KIND_TEST_RUN]
    assert p01.components["evidence_confidence"] > 0.0
    p02 = next(r for r in rows if r.protocol_id == P02)
    assert p02.confidence == "unproven"  # no spillover to other protocols


def test_ac2_telemetry_moves_realized_component(ledger, tel):
    tel.record_fee(P01, fee_usd=100.0, aum_usd=1_000.0, ts=1_000_000.0)
    rows = _ranker(ledger, tel).rank()
    p01 = next(r for r in rows if r.protocol_id == P01)
    assert p01.has_telemetry is True
    assert p01.components["realized"] == pytest.approx(0.10)  # 100/1000 fee rate
    assert p01.detail["realized_source"] == "telemetry_fee_rate"
    assert p01.confidence == "catalog-only"  # telemetry is not ledger evidence


def test_ac2_ranking_is_deterministic(ledger, tel):
    a = _ranker(ledger, tel).rank()
    b = _ranker(ledger, tel).rank()
    assert [r.protocol_id for r in a] == [r.protocol_id for r in b]
    assert [r.score for r in a] == [r.score for r in b]


# -- AC3: kill-switch tick ---------------------------------------------------------------
def test_ac3_negative_roi_triggers_kill_tick():
    ks = KillSwitch()
    d = ks.evaluate(Tick(P02, "2026-W40", -0.02, 1_000_000.0))
    assert isinstance(d, KillDecision)
    assert d.action == "KILL_TICK"
    assert d.tick.realized_roi == -0.02
    assert "advisory" in d.reason
    assert d.executed is False  # advisory only: never executes here
    assert ks.kills() == [d]


def test_ac3_nonnegative_roi_keeps():
    ks = KillSwitch()
    d = ks.evaluate(Tick(P01, "2026-W40", 0.05, 1_000_000.0))
    assert d.action == "KEEP"
    assert ks.kills() == []


def test_ac3_p26_self_kill_is_refused():
    ks = KillSwitch()
    with pytest.raises(KillSwitchError, match="never kills itself"):
        ks.evaluate(Tick(SELF_ID, "2026-W40", -0.99, 1_000_000.0))


def test_ac3_unknown_protocol_tick_is_refused():
    ks = KillSwitch()
    with pytest.raises(KillSwitchError, match="unknown protocol"):
        ks.evaluate(Tick("P99_NOPE", "2026-W40", -0.5, 1_000_000.0))


def test_ac3_roi_observation_path():
    ks = KillSwitch()
    tel2 = Telemetry()
    obs = tel2.record_roi(P02, roi=-0.01, window_s=86400.0, ts=1_000_000.0)
    d = ks.evaluate_roi_observation(obs, "2026-W40")
    assert d.action == "KILL_TICK"


# -- AC4: telemetry records realized fees/ROI ----------------------------------------------
def test_ac4_telemetry_latest_values(tel):
    assert tel.latest_roi(P01) is None
    assert tel.latest_fee_rate(P01) is None
    assert tel.has_realized_data(P01) is False
    tel.record_fee(P01, 50.0, 1_000.0, ts=1_000_000.0)
    tel.record_fee(P01, 150.0, 2_000.0, ts=2_000_000.0)
    assert tel.latest_fee_rate(P01) == pytest.approx(0.075)  # latest only
    tel.record_roi(P01, 0.12, 86_400.0, ts=3_000_000.0)
    assert tel.latest_roi(P01) == 0.12
    assert tel.has_realized_data(P01) is True
    with pytest.raises(ValueError):
        tel.record_fee(P01, -1.0, 1_000.0)  # negative values rejected


# -- AC5: TOA feedback loop stays bounded -------------------------------------------------------
def _entry(pid, score, confidence):
    return ranker.RankEntry(
        protocol_id=pid, score=score, confidence=confidence,
        components={}, evidence_kinds=[], has_telemetry=False, detail={})


def test_ac5_killed_protocols_get_zero_multiplier():
    rows = [_entry(P02, 0.9, "evidenced")]
    kills = [KillDecision(Tick(P02, "2026-W40", -0.05, 1_000_000.0),
                          "KILL_TICK", "advisory", 1_000_000.0)]
    m = feedback(rows, kills)
    assert m[P02] == 0.0


def test_ac5_unproven_protocols_capped_at_5pct():
    rows = [_entry(P01, 0.9, "unproven")]
    m = feedback(rows, [])
    assert m[P01] == pytest.approx(toa_loop.CAP_UNPROVEN) == pytest.approx(0.05)


def test_ac5_top_quartile_evidenced_boosted_within_bounds():
    rows = [_entry(f"P{i:02d}_X", 1.0 - i * 0.01, "evidenced") for i in range(8)]
    m = feedback(rows, [])
    # quartile = max(1, 8//4) = 2 -> first two boosted by exactly 10%
    assert m["P00_X"] == pytest.approx(1.0 + toa_loop.BOOST_TOP_QUARTILE)
    assert m["P01_X"] == pytest.approx(1.0 + toa_loop.BOOST_TOP_QUARTILE)
    assert m["P02_X"] == pytest.approx(1.0)
    assert max(m.values()) <= 1.0 + toa_loop.BOOST_TOP_QUARTILE


def test_ac5_feedback_is_stateless_pure_function():
    rows = [_entry(P01, 0.5, "evidenced")]
    assert feedback(rows, []) == feedback(rows, [])


# -- AC6: proof hooks ------------------------------------------------------------------------------
def test_ac6_ledger_attachment_is_a_real_reference(ledger):
    from src.sincor2.defi.products import mint_sku
    sku = mint_sku(P02)
    entry = ledger.append(sku, KIND_TEST_RUN,
                          {"command": "pytest", "passed": 10, "failed": 0})
    d = KillDecision(Tick(P02, "2026-W40", -0.05, 1_000_000.0),
                     "KILL_TICK", "advisory", 1_000_000.0)
    out = proof_hooks.attach_evidence(d, ledger)
    assert out.evidence_refs == [entry["entry_id"]]  # real ledger entry id
    assert entry["entry_id"].startswith("ev_")


def test_ac6_sinax_unavailable_is_honest():
    status = proof_hooks.sinax_status()
    assert status["available"] is False
    assert "does not exist" in status["reason"] or "not present" in status["reason"]


def test_ac6_decision_package_is_serializable(ledger):
    d = KillDecision(Tick(P02, "2026-W40", -0.05, 1_000_000.0),
                     "KILL_TICK", "advisory", 1_000_000.0)
    pkg = proof_hooks.decision_package(proof_hooks.attach_evidence(d, ledger))
    assert pkg["protocol_id"] == P02 and pkg["action"] == "KILL_TICK"


# -- gate-stage evidence: the ranker consumes gates.evaluate() ---------------------------------------
def test_gate_evidence_runs_the_real_gate(ledger):
    from src.sincor2.defi.products import mint_sku
    # spec -> build: the deep spec is on file, so this gate honestly passes.
    reg = [{"sku": mint_sku(P01), "protocol_id": P01, "stage": "spec",
            "name": "Yield Aggregator Vault"}]
    ev = gate_stage_evidence(P01, ledger, str(ROOT), registry=reg)
    assert ev["stage"] == "spec"
    assert ev["next_stage"] == "build"  # STAGES order: spec -> build -> test
    assert ev["gate_ok"] is True
    assert ev["failed_checks"] == []
    # build -> test: the empty ledger has no unit-test evidence, so the same
    # gate machine honestly refuses with the real check name and reason.
    reg2 = [{"sku": mint_sku(P01), "protocol_id": P01, "stage": "build",
             "name": "Yield Aggregator Vault"}]
    ev2 = gate_stage_evidence(P01, ledger, str(ROOT), registry=reg2)
    assert ev2["stage"] == "build"
    assert ev2["next_stage"] == "test"
    assert ev2["gate_ok"] is False
    assert ev2["failed_checks"] == ["unit_tests_passing"]
    assert ev2["reasons"]
    assert set(ev2["evidence"]) >= set(ev2["failed_checks"])


def test_gate_evidence_unknown_product_is_explicit(ledger):
    ev = gate_stage_evidence("P99_NOPE", ledger, str(ROOT), registry=[])
    assert ev["gate_ok"] is False
    assert ev["reasons"] == ["unknown product"]


def test_rank_rows_carry_gate_stage_detail(ledger, tel):
    rows = _ranker(ledger, tel).rank()
    for row in rows:
        gate = row.detail["gate_stage"]
        assert gate["stage"] in ("spec", "build", "test", "audit", "product", "catalog")
        assert "gate_ok" in gate and "failed_checks" in gate
    # and the API surfaces it
    api_view = DefiOSApi(ledger, tel)
    snap = api_view.rankings()
    assert len(snap) == 25
    assert all("gate_stage" in r for r in snap)


# -- API: read-only dashboard ----------------------------------------------------------------------------
def test_api_dashboard_is_read_only(ledger, tel):
    dash = DefiOSApi(ledger, tel)
    snap = dash.rankings()
    assert len(snap) == 25
    posture = dash.posture()
    assert posture["ranked_protocols"] == 25
    assert isinstance(posture["live_blocked"], list)
    mults = dash.next_cycle_multipliers()
    assert set(mults) == {r["protocol_id"] for r in snap}
    assert all(v == 0.05 for v in mults.values())  # all unproven -> capped
    assert dash.kill_decisions() == []
    assert not hasattr(dash, "write")  # no write path exists
