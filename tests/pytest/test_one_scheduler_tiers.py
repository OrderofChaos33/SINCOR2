"""One-scheduler STREAM C tests: effect tiers, idempotency, kill-switch,
require_approval mode.

Covers:
1. job_tier() classification for all 12 canonical jobs.
2. Idempotency: a Tier 2/3 job's effect function runs once per hourly bucket;
   the duplicate firing is skipped with an "idempotent skip" log and the
   effect function is not invoked again. (Sub-hourly jobs like
   activity_engine_cycle use a per-job idempotency_minutes bucket.)
3. Kill-switch (SCHEDULER_EFFECTS_PAUSED=true): Tier 2/3 jobs skipped, Tier
   0/1 jobs unwrapped/unaffected.
4. require_approval mode (SCHEDULER_REQUIRE_APPROVAL=true): Tier 2/3 jobs
   produce an intent record (job_id, run_id, timestamp, would_have_done)
   without invoking the effect function.
"""
from datetime import datetime, timezone

import pytest

import sincor2.scheduler as central
from sincor2 import scheduler_idempotency as idem
from sincor2 import scheduler_tiers as tiers

EXPECTED_TIERS = {
    "daily_ops": 0,
    "sadas_scan": 0,
    "compliance_checks": 0,
    "content_cycle": 1,
    "launch_cycle": 1,
    "recursive_optimizer": 1,
    "outreach_cycle": 2,
    "subscription_reminders": 2,
    "launch_review_notify": 2,
    "partner_notify": 2,
    "activity_engine_cycle": 2,
    "polyclaw_scan": 3,
}

TIER2_JOB = "outreach_cycle"
TIER3_JOB = "polyclaw_scan"
TIER0_JOB = "sadas_scan"
TIER1_JOB = "content_cycle"


@pytest.fixture()
def clean_state(monkeypatch):
    """Neutral env + fresh idempotency/intent stores for every test."""
    monkeypatch.delenv("SCHEDULER_EFFECTS_PAUSED", raising=False)
    monkeypatch.delenv("SCHEDULER_REQUIRE_APPROVAL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    idem._IN_MEMORY.clear()
    idem.clear_intent_records()
    yield
    idem._IN_MEMORY.clear()
    idem.clear_intent_records()


def _fake_effect(calls):
    def _effect():
        calls.append(datetime.now(timezone.utc))
    _effect.__name__ = "fake_effect"
    return _effect


# --- 1. tier lookup ----------------------------------------------------------

def test_tier_lookup_all_twelve_jobs():
    assert set(EXPECTED_TIERS) == set(central.CANONICAL_JOB_IDS)
    for job_id, expected in EXPECTED_TIERS.items():
        assert tiers.job_tier(job_id) == expected, job_id


def test_tier_lookup_unknown_job_raises():
    with pytest.raises(KeyError):
        tiers.job_tier("not_a_job")


def test_tier_names_cover_all_tiers():
    for tier in (0, 1, 2, 3):
        assert isinstance(tiers.tier_name(tier), str)


# --- 2. idempotency ----------------------------------------------------------

def test_idempotency_skip_on_duplicate_key(clean_state, monkeypatch, caplog):
    fixed = datetime(2026, 10, 12, 1, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(idem, "utcnow", lambda: fixed)

    calls = []
    guarded = central._wrap_job(TIER2_JOB, _fake_effect(calls))

    with caplog.at_level("INFO", logger="sincor2.scheduler"):
        guarded()   # first firing runs
    assert len(calls) == 1

    with caplog.at_level("INFO", logger="sincor2.scheduler"):
        guarded()   # duplicate firing in same hourly bucket skips
    assert len(calls) == 1
    assert "idempotent skip" in caplog.text


def test_idempotency_fresh_key_next_bucket(clean_state, monkeypatch):
    calls = []
    guarded = central._wrap_job(TIER2_JOB, _fake_effect(calls))

    monkeypatch.setattr(idem, "utcnow",
                        lambda: datetime(2026, 10, 12, 1, 45, tzinfo=timezone.utc))
    guarded()
    monkeypatch.setattr(idem, "utcnow",
                        lambda: datetime(2026, 10, 12, 2, 5, tzinfo=timezone.utc))
    guarded()  # next hourly bucket -> runs again
    assert len(calls) == 2


def test_idempotency_tier3_job_also_guarded(clean_state, monkeypatch):
    monkeypatch.setattr(idem, "utcnow",
                        lambda: datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc))
    calls = []
    guarded = central._wrap_job(TIER3_JOB, _fake_effect(calls))
    guarded()
    guarded()
    assert len(calls) == 1


def test_idempotency_key_format():
    key = idem.make_key("outreach_cycle",
                        datetime(2026, 10, 12, 7, 59, tzinfo=timezone.utc))
    assert key == "outreach_cycle:2026101207"


def test_idempotency_key_sub_hourly_bucket():
    # Jobs with idempotency_minutes < 60 (e.g. the 2-minute activity
    # engine loop) get a per-bucket key so the intended cadence is not
    # throttled, while duplicates inside the same bucket still collide.
    t1 = datetime(2026, 10, 12, 7, 1, 30, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 12, 7, 3, 30, tzinfo=timezone.utc)
    t3 = datetime(2026, 10, 12, 7, 1, 45, tzinfo=timezone.utc)
    k1 = idem.make_key("activity_engine_cycle", t1, bucket_minutes=2)
    k2 = idem.make_key("activity_engine_cycle", t2, bucket_minutes=2)
    k3 = idem.make_key("activity_engine_cycle", t3, bucket_minutes=2)
    assert k1 == "activity_engine_cycle:2026101207:00"
    assert k2 == "activity_engine_cycle:2026101207:02"
    assert k3 == k1  # same 2-minute bucket -> duplicate skipped
    # Default (no bucket arg) stays hourly for all existing jobs.
    assert idem.make_key("outreach_cycle", t1) == "outreach_cycle:2026101207"


# --- 3. kill-switch ----------------------------------------------------------

def test_kill_switch_skips_tier2_and_tier3(clean_state, monkeypatch, caplog):
    monkeypatch.setenv("SCHEDULER_EFFECTS_PAUSED", "true")

    for job_id in (TIER2_JOB, TIER3_JOB):
        calls = []
        guarded = central._wrap_job(job_id, _fake_effect(calls))
        with caplog.at_level("WARNING", logger="sincor2.scheduler"):
            guarded()
        assert calls == [], job_id
    assert "SCHEDULER_EFFECTS_PAUSED" in caplog.text


def test_kill_switch_leaves_tier0_and_tier1_unaffected(clean_state, monkeypatch):
    monkeypatch.setenv("SCHEDULER_EFFECTS_PAUSED", "true")

    # Tier 0/1 jobs are returned unwrapped — identity check proves no guard.
    for job_id in (TIER0_JOB, TIER1_JOB):
        func = _fake_effect([])
        assert central._wrap_job(job_id, func) is func

    # And they still run normally under the kill-switch.
    calls = []
    wrapped = central._wrap_job(TIER0_JOB, _fake_effect(calls))
    wrapped()
    assert len(calls) == 1


# --- 4. require_approval mode --------------------------------------------------

def test_require_approval_records_intent_without_effect(clean_state, monkeypatch, caplog):
    monkeypatch.setenv("SCHEDULER_REQUIRE_APPROVAL", "true")

    calls = []
    guarded = central._wrap_job(TIER2_JOB, _fake_effect(calls))
    with caplog.at_level("INFO", logger="sincor2.scheduler"):
        guarded()

    assert calls == []  # effect function never invoked
    records = idem.list_intent_records()
    assert len(records) == 1
    record = records[0]
    assert record["job_id"] == TIER2_JOB
    assert record["run_id"]          # non-empty
    assert record["timestamp"]       # non-empty
    assert record["would_have_done"]  # non-empty summary
    assert "SCHEDULER_REQUIRE_APPROVAL" in caplog.text


def test_require_approval_tier3_also_draft_only(clean_state, monkeypatch):
    monkeypatch.setenv("SCHEDULER_REQUIRE_APPROVAL", "true")

    calls = []
    guarded = central._wrap_job(TIER3_JOB, _fake_effect(calls))
    guarded()
    guarded()

    assert calls == []  # no effects in approval mode, both firings
    records = idem.list_intent_records()
    assert len(records) == 2
    assert records[0]["run_id"] != records[1]["run_id"]  # distinct run ids


def test_normal_mode_runs_effect_and_marks_key(clean_state, monkeypatch):
    monkeypatch.setattr(idem, "utcnow",
                        lambda: datetime(2026, 10, 12, 9, 15, tzinfo=timezone.utc))
    calls = []
    guarded = central._wrap_job(TIER2_JOB, _fake_effect(calls))
    guarded()
    assert len(calls) == 1
    assert idem.already_done("outreach_cycle:2026101209")
    assert idem.list_intent_records() == []
