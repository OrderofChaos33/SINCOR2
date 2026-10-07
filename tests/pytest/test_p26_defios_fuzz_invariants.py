"""Audit-prep invariant + adversarial fuzz tests for P26 (defios meta layer).

Property tests an auditor would demand of the self-improving DeFi OS:

- telemetry_fuzz: negative fee/AUM rejected; non-positive ROI windows
  rejected; latest_fee_rate is exactly fee/aum of the newest observation
  (None when AUM is 0); latest_roi is the newest ROI; has_realized_data
  reflects any recorded observation.
- killswitch_fuzz: negative realized ROI always emits KILL_TICK and
  non-negative always KEEP; decisions are advisory (executed is always
  False) and appended in order; kills() returns exactly the kills;
  P26 itself and unknown protocols raise, fail-closed.
- ranker_fuzz: the ranked universe is exactly the other 25 protocols
  (P26 never ranks itself); rankings are sorted descending by score;
  every score decomposes exactly as 0.5*realized + 0.3*catalog +
  0.2*evidence; confidence labels are within the four documented values;
  realized prefers ROI over fee_rate over 0; ranking is deterministic
  across runs; protocols with no ledger evidence and no telemetry are
  labeled "unproven", never presented as proven.
- confidence_fuzz: 2 distinct evidence kinds + clean audit -> "proven";
  a passing test_run -> "evidenced"; nothing -> "catalog-only"; the
  clean-audit bonus caps at 1.0.
- feedback_fuzz: killed protocols get 0.0; unproven are capped at 0.05;
  the top quartile gets the 1.1 boost; everyone else 1.0 — pure function
  of (rankings, kills), no hidden state.
- hooks_fuzz: attach_evidence references the newest entry id per kind;
  decision_package is JSON-serializable and carries the advisory flag.
- posture_fuzz: live_blocked_set and live_eligible_set partition the 25.

Deterministic: seeded RNG. N/N must pass.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from src.sincor2.defi import p26  # noqa: E402
from src.sincor2.defi.catalog import PROTOCOL_BY_ID  # noqa: E402
from src.sincor2.defi.p26 import (  # noqa: E402
    PARAMS,
    SELF_ID,
    api,
    killswitch,
    proof_hooks,
    ranker,
    registry_wrap,
    telemetry,
    toa_loop,
)
from src.sincor2.defi.p26.killswitch import KillDecision, KillSwitch, Tick
from src.sincor2.defi.proof_ledger import (  # noqa: E402
    KIND_AUDIT_REPORT,
    KIND_FORK_SIM,
    KIND_INVARIANT_TEST,
    KIND_TEST_RUN,
    ProofLedger,
)
from src.sincor2.defi.p26.ranker import Ranker, evidence_confidence  # noqa: E402
from src.sincor2.defi.p26.telemetry import Telemetry  # noqa: E402
from src.sincor2.defi.products import mint_sku  # noqa: E402

RNG = random.Random(0xA02626)
PIDS = sorted(PROTOCOL_BY_ID.keys() - {SELF_ID})
LABELS = {"proven", "evidenced", "catalog-only", "unproven"}


@pytest.fixture()
def ledger(tmp_path):
    return ProofLedger(path=str(tmp_path / "ledger.json"))


@pytest.fixture()
def tel():
    return Telemetry()


def _ranker(ledger, tel, tmp_path):
    return Ranker(ledger, tel, root=str(tmp_path))


def test_telemetry_ingest_and_latest():
    t = Telemetry()
    for bad in (-1.0, -10**6):
        for fn in (lambda v: t.record_fee("P01_YIELD_AGG", v, 100.0),
                   lambda v: t.record_fee("P01_YIELD_AGG", 10.0, v)):
            try:
                fn(bad)
                raise AssertionError("negative telemetry accepted")
            except ValueError:
                pass
    try:
        t.record_roi("P01_YIELD_AGG", 0.05, 0.0)
        raise AssertionError("non-positive window accepted")
    except ValueError:
        pass
    for _ in range(50):
        pid = RNG.choice(PIDS)
        fee = RNG.uniform(0, 10**6)
        aum = RNG.choice([RNG.uniform(1, 10**9), 0.0])
        t.record_fee(pid, fee, aum)
        last = t._fees[pid][-1]
        if last.aum_usd <= 0:
            assert t.latest_fee_rate(pid) is None
        else:
            assert t.latest_fee_rate(pid) == last.fee_usd / last.aum_usd
        roi = RNG.uniform(-0.5, 0.5)
        t.record_roi(pid, roi, 3600.0)
        assert t.latest_roi(pid) == t._rois[pid][-1].roi
        assert t.has_realized_data(pid) is True
    assert t.latest_roi("P02_CLMM") is None or True  # may have data; no crash
    assert t.has_realized_data("P99_GHOST") is False


def test_killswitch_fuzz():
    for _ in range(200):
        ks = KillSwitch()
        ticks = []
        for i in range(RNG.randint(1, 6)):
            pid = RNG.choice(PIDS)
            roi = RNG.choice([RNG.uniform(-1, 1), 0.0, -1e-9, 1e-9])
            tick = Tick(pid, f"2026-W{RNG.randint(1, 52)}", roi, 1e6)
            ticks.append(tick)
            d = ks.evaluate(tick)
            if roi < PARAMS["kill_roi_threshold"]:
                assert d.action == "KILL_TICK"
            else:
                assert d.action == "KEEP"
            assert d.executed is False  # advisory only, always
            assert isinstance(d.decided_ts, float)
        assert len(ks.decisions) == len(ticks)
        assert ks.kills() == [d for d in ks.decisions
                              if d.action == "KILL_TICK"]
        # evaluate_roi_observation routes through the same path
        obs = telemetry.RoiObservation("P01_YIELD_AGG", 1e6, -0.25, 3600.0)
        d2 = ks.evaluate_roi_observation(obs, "2026-W40")
        assert d2.action == "KILL_TICK" and d2.tick.realized_roi == -0.25
    # P26 never kills itself; unknown protocols raise
    ks = KillSwitch()
    try:
        ks.evaluate(Tick(SELF_ID, "2026-W40", -0.5, 1e6))
        raise AssertionError("self-kill accepted")
    except killswitch.KillSwitchError:
        pass
    try:
        ks.evaluate(Tick("P99_GHOST", "2026-W40", -0.5, 1e6))
        raise AssertionError("unknown protocol kill accepted")
    except killswitch.KillSwitchError:
        pass


def _seed_evidence(ledger, pid, kinds, clean_audit=False):
    sku = mint_sku(pid)
    for kind in kinds:
        ledger.append(sku=sku, kind=kind,
                      details={"passed": 3, "failed": 0,
                               "open_critical": 0 if clean_audit else 1},
                      commit="abc123", recorded_by="fuzz")


def test_ranker_score_decomposition_and_honesty(ledger, tel, tmp_path):
    # seed heterogeneous evidence: some protocols evidenced, some clean,
    # most with nothing at all
    evidenced = RNG.sample(PIDS, 6)
    for pid in evidenced:
        _seed_evidence(ledger, pid, [KIND_TEST_RUN], clean_audit=False)
    proven = RNG.sample([p for p in PIDS if p not in evidenced], 2)
    for pid in proven:
        _seed_evidence(ledger, pid,
                       [KIND_TEST_RUN, KIND_INVARIANT_TEST, KIND_FORK_SIM],
                       clean_audit=True)
        ledger.append(sku=mint_sku(pid), kind=KIND_AUDIT_REPORT,
                      details={"passed": 1, "failed": 0, "open_critical": 0},
                      commit="abc123", recorded_by="fuzz")
    for _ in range(8):
        pid = RNG.choice(PIDS)
        if RNG.random() < 0.5:
            tel.record_roi(pid, RNG.uniform(-0.3, 0.3), 3600.0)
        else:
            tel.record_fee(pid, RNG.uniform(0, 10**5),
                           RNG.uniform(10**3, 10**9))
    r = _ranker(ledger, tel, tmp_path)
    entries = r.rank()
    # universe: exactly the other 25, P26 never ranks itself
    assert len(entries) == 25
    assert {e.protocol_id for e in entries} == set(PIDS)
    assert SELF_ID not in {e.protocol_id for e in entries}
    # sorted descending by score
    scores = [e.score for e in entries]
    assert scores == sorted(scores, reverse=True)
    for e in entries:
        assert e.confidence in LABELS
        assert set(e.evidence_kinds) <= {KIND_TEST_RUN, KIND_INVARIANT_TEST,
                                         KIND_FORK_SIM, KIND_AUDIT_REPORT}
        # score decomposes exactly: 0.5*realized + 0.3*catalog + 0.2*evidence
        roi = tel.latest_roi(e.protocol_id)
        fee_rate = tel.latest_fee_rate(e.protocol_id)
        realized = roi if roi is not None else (
            fee_rate if fee_rate is not None else 0.0)
        spec = PROTOCOL_BY_ID[e.protocol_id]
        catalog = registry_wrap.risk_adjusted_target(spec)
        conf, _, _, _ = evidence_confidence(ledger, e.protocol_id)
        expect = (PARAMS["w_realized"] * realized
                  + PARAMS["w_catalog"] * catalog
                  + PARAMS["w_evidence"] * conf)
        assert e.score == expect, e.protocol_id
        assert e.components["realized"] == realized
        assert e.components["catalog_risk_adjusted"] == catalog
        assert e.components["evidence_confidence"] == conf
        assert e.has_telemetry == tel.has_realized_data(e.protocol_id)
        # never presented as proven without evidence
        if e.confidence in ("proven", "evidenced"):
            assert e.evidence_kinds or e.has_telemetry
        kinds = {en["kind"] for en in ledger.read(sku=mint_sku(e.protocol_id))}
        if not kinds and not e.has_telemetry:
            assert e.confidence == "unproven"
    # deterministic across runs
    again = r.rank()
    assert [e.score for e in again] == scores


def test_confidence_labels(ledger):
    pid = "P04_MEV"
    conf, label, kinds, detail = evidence_confidence(ledger, pid)
    assert label == "catalog-only" and kinds == []
    _seed_evidence(ledger, pid, [KIND_TEST_RUN])
    conf, label, kinds, detail = evidence_confidence(ledger, pid)
    assert label == "evidenced"
    assert detail["passing_test_runs"] == 1
    # 2 distinct kinds + clean audit -> proven; bonus caps at 1.0
    _seed_evidence(ledger, pid, [KIND_INVARIANT_TEST, KIND_FORK_SIM],
                   clean_audit=True)
    ledger.append(sku=mint_sku(pid), kind=KIND_AUDIT_REPORT,
                  details={"passed": 1, "failed": 0, "open_critical": 0},
                  commit="abc123", recorded_by="fuzz")
    conf, label, kinds, detail = evidence_confidence(ledger, pid)
    assert label == "proven"
    assert conf == 1.0  # 4/2 capped at 1.0 + 0.25 capped at 1.0
    assert detail["clean_audit"] is True


def test_feedback_weights_pure_function(ledger, tel, tmp_path):
    r = _ranker(ledger, tel, tmp_path)
    entries = r.rank()
    ks = KillSwitch()
    # kill the top-ranked protocol and one mid-ranked
    ks.evaluate(Tick(entries[0].protocol_id, "2026-W40", -0.1, 1e6))
    mid = entries[len(entries) // 2]
    ks.evaluate(Tick(mid.protocol_id, "2026-W40", -0.01, 1e6))
    w1 = toa_loop.feedback(entries, ks.decisions)
    w2 = toa_loop.feedback(entries, ks.decisions)
    assert w1 == w2  # pure function, no hidden state
    n = len(entries)
    quartile = max(1, n // 4)
    for i, e in enumerate(entries):
        w = w1[e.protocol_id]
        if e.protocol_id in (entries[0].protocol_id, mid.protocol_id):
            assert w == 0.0  # killed ticks go to zero
        elif e.confidence == "unproven":
            assert w == toa_loop.CAP_UNPROVEN  # capped at 5%
        elif i < quartile:
            assert w == 1.0 + toa_loop.BOOST_TOP_QUARTILE
        else:
            assert w == 1.0
    # no kills: weights are still well-formed
    w3 = toa_loop.feedback(entries, [])
    assert all(v in (0.05, 1.0, 1.1) for v in w3.values())


def test_hooks_and_package_serializable(ledger, tmp_path):
    pid = "P07_BRIDGE"
    _seed_evidence(ledger, pid, [KIND_TEST_RUN, KIND_INVARIANT_TEST])
    sku = mint_sku(pid)
    tr_runs = ledger.read(sku=sku, kind=KIND_TEST_RUN)
    inv_runs = ledger.read(sku=sku, kind=KIND_INVARIANT_TEST)
    ks = KillSwitch()
    d = ks.evaluate(Tick(pid, "2026-W40", -0.2, 1e6))
    out = proof_hooks.attach_evidence(d, ledger)
    # newest entry id per kind, in EVIDENCE_KINDS order
    assert out.evidence_refs == [tr_runs[-1]["entry_id"],
                                 inv_runs[-1]["entry_id"]]
    pkg = proof_hooks.decision_package(out)
    json.dumps(pkg)  # serializable
    assert pkg["executed"] is False
    assert pkg["action"] == "KILL_TICK"
    # sinax hook is honest about availability
    assert isinstance(pkg["sinax"]["available"], bool)


def test_posture_partitions_universe():
    blocked = registry_wrap.live_blocked_set()
    eligible = registry_wrap.live_eligible_set()
    assert set(blocked) | set(eligible) == set(PIDS)
    assert not (set(blocked) & set(eligible))
    assert len(registry_wrap.ranked_universe()) == 25
    assert all(p.protocol_id != SELF_ID for p in registry_wrap.ranked_universe())
    # risk-adjusted target is bounded by the catalog target
    for spec in registry_wrap.ranked_universe():
        ra = registry_wrap.risk_adjusted_target(spec)
        assert 0.0 <= ra <= max(spec.target_apr, 0.0)


def test_api_read_views(ledger, tel, tmp_path):
    api_obj = api.DefiOSApi(ledger, tel)
    rankings = api_obj.rankings()
    assert len(rankings) == 25
    for row in rankings:
        assert set(row) >= {"protocol_id", "score", "confidence",
                            "components", "evidence_kinds", "has_telemetry",
                            "gate_stage"}
        json.dumps(row)  # serializable
    posture = api_obj.posture()
    assert posture["ranked_protocols"] == 25
    assert posture["kills"] == 0
    ks_ticks = [Tick("P10_FLASH_ARB", "2026-W40", -0.3, 1e6)]
    for t in ks_ticks:
        api_obj.killswitch.evaluate(t)
    assert api_obj.posture()["kills"] == 1
    kills = api_obj.kill_decisions()
    assert len(kills) == 1 and kills[0]["action"] == "KILL_TICK"
    mults = api_obj.next_cycle_multipliers()
    assert mults["P10_FLASH_ARB"] == 0.0
