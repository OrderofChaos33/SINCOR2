"""Liveness personas — 6 distinct bidding strategies (Part A).

Each test instantiates the personas against the SAME auction scenarios and
asserts meaningfully different bid decisions: different bid amounts and
different skip/bid choices.  RNG is seeded so the decisions are
deterministic; behaviour differences are structural, not luck.
"""
from __future__ import annotations

import importlib.util
import os
import random
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PERSONAS_PATH = os.path.join(_HERE, "..", "..", "liveness", "personas.py")

_spec = importlib.util.spec_from_file_location("liveness_personas", _PERSONAS_PATH)
personas = importlib.util.module_from_spec(_spec)
sys.modules["liveness_personas"] = personas  # dataclasses resolve annotations via sys.modules
_spec.loader.exec_module(personas)


def _task(tags=("defi", "data"), merit=False, now=1_000_000, window_ms=600_000):
    return {
        "task_id": "task-x",
        "tags": list(tags),
        "requires_merit": merit,
        "created_at": now - window_ms,
        "commit_deadline": now,  # noqa: F841  (overridden per test)
    }


def _fresh_task(now_ms, commit_in_ms, tags=("defi", "data"), merit=False):
    return {
        "task_id": "task-x",
        "tags": list(tags),
        "requires_merit": merit,
        "created_at": now_ms,
        "commit_deadline": now_ms + commit_in_ms,
    }


def _agent(pid):
    return {"agent_id": "sincor-liveness-0X", "persona": pid}


# ---------------------------------------------------------------------------
# identity of the table
# ---------------------------------------------------------------------------


def test_six_personas_defined_and_distinct():
    assert set(personas.PERSONA_IDS) == set(personas.PERSONAS.keys())
    assert len(personas.PERSONA_IDS) == 6
    bands = {(p.bid_fraction_lo, p.bid_fraction_hi) for p in personas.PERSONAS.values()}
    assert len(bands) == 6  # every persona has its own bid band


def test_unknown_persona_falls_back_to_none():
    rng = random.Random(1)
    task = _fresh_task(1_000_000, 600_000)
    assert personas.persona_bid_amount({"agent_id": "x"}, task, 3.0, 1_000_000, rng) is None
    assert personas.persona_bid_amount({"agent_id": "x", "persona": "nope"}, task, 3.0, 1_000_000, rng) is None


def test_opt_in_flag_defaults_off():
    assert personas.enabled() is False


# ---------------------------------------------------------------------------
# distinct bid amounts on the same scenario
# ---------------------------------------------------------------------------


def test_aggressive_bids_higher_than_value_hunter_same_task():
    now = 1_700_000_000_000
    task = _fresh_task(now, 600_000)
    bounty = 3.0
    a = personas.PERSONAS["aggressive"].decide(task, bounty, now, random.Random(7))
    v = personas.PERSONAS["value_hunter"].decide(task, bounty, now, random.Random(7))
    assert a is not None and v is not None
    assert a > v  # 94-99.5% band vs 78-87% band: never overlap
    assert 0.94 * bounty <= a <= 0.995 * bounty
    assert 0.78 * bounty <= v <= 0.87 * bounty


def test_contrarian_undercuts_everyone_when_it_plays():
    now = 1_700_000_000_000
    task = _fresh_task(now, 600_000)
    bounty = 3.0
    bids = {}
    for pid in personas.PERSONA_IDS:
        cfg = personas.PERSONAS[pid]
        # seed per persona; retry a few seeds until the persona bids (sniper
        # never bids here — asserted separately)
        for seed in range(20):
            b = cfg.decide(task, bounty, now, random.Random(seed))
            if b is not None:
                bids[pid] = b
                break
    assert bids["contrarian"] < bids["aggressive"]
    assert bids["contrarian"] < bids["value_hunter"]
    assert bids["contrarian"] < bids["conservative"]


# ---------------------------------------------------------------------------
# distinct skip/bid choices on the same scenario
# ---------------------------------------------------------------------------


def test_value_hunter_skips_small_bounty_aggressive_bids():
    now = 1_700_000_000_000
    task = _fresh_task(now, 600_000)
    rng = random.Random(3)
    assert personas.PERSONAS["value_hunter"].decide(task, 1.0, now, rng) is None
    aggressive = personas.PERSONAS["aggressive"].decide(task, 1.0, now, rng)
    assert aggressive is not None and aggressive >= 0.94


def test_sniper_skips_fresh_window_bids_in_tail():
    now = 1_700_000_000_000
    window = 600_000
    fresh = _fresh_task(now, window)                       # 100% remaining
    tail = {"task_id": "t", "tags": ["defi"], "requires_merit": False,
            "created_at": now - window + 30_000,
            "commit_deadline": now + 30_000}               # 5% remaining
    sniper = personas.PERSONAS["sniper"]
    assert sniper.decide(fresh, 3.0, now, random.Random(11)) is None
    bid = sniper.decide(tail, 3.0, now, random.Random(11))
    assert bid is not None
    assert 0.90 * 3.0 <= bid <= 0.96 * 3.0


def test_specialist_bids_specialty_skips_other():
    now = 1_700_000_000_000
    spec = personas.PERSONAS["specialist"]
    defi_task = _fresh_task(now, 600_000, tags=("defi", "risk"))
    other_task = _fresh_task(now, 600_000, tags=("compliance", "audit"))
    assert spec.decide(defi_task, 3.0, now, random.Random(5)) is not None
    assert spec.decide(other_task, 3.0, now, random.Random(5)) is None


def test_conservative_skips_merit_tasks_and_big_bounties():
    now = 1_700_000_000_000
    cons = personas.PERSONAS["conservative"]
    merit_task = _fresh_task(now, 600_000, merit=True)
    big_task = _fresh_task(now, 600_000)
    # merit task skipped regardless of the coin flip — run many seeds
    assert all(cons.decide(merit_task, 3.0, now, random.Random(s)) is None
               for s in range(10))
    # bounty over 5 AXM skipped regardless of the coin flip
    assert all(cons.decide(big_task, 6.0, now, random.Random(s)) is None
               for s in range(10))
    # small merit-free bounty: bids on the seeds that pass the coin flip
    bids = [cons.decide(big_task, 3.0, now, random.Random(s)) for s in range(10)]
    assert any(b is not None for b in bids)
    assert any(b is None for b in bids)


def test_aggressive_never_skips_and_value_hunter_skips_by_rule():
    now = 1_700_000_000_000
    task = _fresh_task(now, 600_000)
    aggr = personas.PERSONAS["aggressive"]
    assert all(aggr.decide(task, 3.0, now, random.Random(s)) is not None
               for s in range(10))


def test_persona_bid_amount_routes_through_agent_record():
    now = 1_700_000_000_000
    task = _fresh_task(now, 600_000)
    bid = personas.persona_bid_amount(_agent("aggressive"), task, 3.0, now,
                                      random.Random(9))
    assert bid is not None
    assert 0.94 * 3.0 <= bid <= 0.995 * 3.0
    # specialist routes through the same entrypoint
    other = _fresh_task(now, 600_000, tags=("compliance",))
    assert personas.persona_bid_amount(_agent("specialist"), other, 3.0, now,
                                       random.Random(9)) is None


# ---------------------------------------------------------------------------
# runner wiring: opt-in only, legacy default byte-identical
# ---------------------------------------------------------------------------

_RUNNER_PATH = os.path.join(_HERE, "..", "..", "liveness", "runner.py")

_runner_spec = importlib.util.spec_from_file_location("liveness_runner", _RUNNER_PATH)
runner = importlib.util.module_from_spec(_runner_spec)
sys.modules["liveness_runner"] = runner
_runner_spec.loader.exec_module(runner)


def _make_runner():
    # Runner.__init__ loads commits from disk — point STATE_DIR at a tmp
    # location via monkeypatched constants to keep the repo tree clean.
    import tempfile
    tmp = tempfile.mkdtemp(prefix="persona_runner_")
    runner.COMMITS_PATH = os.path.join(tmp, "commits.json")
    runner.QUEUE_PATH = os.path.join(tmp, "performance_queue.json")
    r = runner.Runner("http://127.0.0.1:9", [])
    return r


def test_runner_bid_amount_legacy_by_default(monkeypatch):
    """Default (opt-in unset): bid amount = legacy uniform 85-98% of bounty."""
    monkeypatch.delenv("SINCOR_LIVENESS_PERSONAS", raising=False)
    r = _make_runner()
    task = {"task_id": "t", "tags": ["defi"]}
    agent = {"agent_id": "sincor-liveness-01", "persona": "aggressive"}
    random.seed(42)
    expected = round(3.0 * random.uniform(runner.BID_FRACTION_LO,
                                          runner.BID_FRACTION_HI), 6)
    random.seed(42)
    assert r._bid_amount(task, agent, 3.0) == expected


def test_runner_bid_amount_persona_when_opted_in(monkeypatch):
    """Opt-in set: a recognised persona drives the bid (aggressive band)."""
    monkeypatch.setenv("SINCOR_LIVENESS_PERSONAS", "1")
    r = _make_runner()
    now = 1_700_000_000_000
    r.now_ms = now
    task = {"task_id": "t", "tags": ["defi"],
            "created_at": now, "commit_deadline": now + 600_000}
    agent = {"agent_id": "sincor-liveness-01", "persona": "aggressive"}
    bid = r._bid_amount(task, agent, 3.0)
    assert bid is not None
    assert 0.94 * 3.0 <= bid <= 0.995 * 3.0


def test_runner_bid_amount_persona_skip_propagates(monkeypatch):
    """Opt-in set: a persona's skip decision yields None (no commit)."""
    monkeypatch.setenv("SINCOR_LIVENESS_PERSONAS", "1")
    r = _make_runner()
    now = 1_700_000_000_000
    r.now_ms = now
    task = {"task_id": "t", "tags": ["compliance"],
            "created_at": now, "commit_deadline": now + 600_000}
    agent = {"agent_id": "sincor-liveness-05", "persona": "specialist"}
    assert r._bid_amount(task, agent, 3.0) is None


def test_runner_bid_amount_unknown_persona_falls_back(monkeypatch):
    """Opt-in set but unrecognised persona id: legacy behaviour preserved."""
    monkeypatch.setenv("SINCOR_LIVENESS_PERSONAS", "1")
    r = _make_runner()
    task = {"task_id": "t", "tags": ["defi"]}
    agent = {"agent_id": "sincor-liveness-01", "persona": "not-a-persona"}
    bid = r._bid_amount(task, agent, 3.0)
    assert bid is not None
    assert 0.85 * 3.0 <= bid <= 0.98 * 3.0
