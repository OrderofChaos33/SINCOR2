"""One-scheduler STREAM B tests: durable job store, run ledger, crash recovery,
misfire/coalesce flags.

Covers:
1. Job-store factory falls back to in-memory MemoryJobStore when DATABASE_URL
   is unset or the DB is unreachable — boot never crashes.
2. The ledger records ok/error/timeout runs (memory ring buffer + SQLAlchemy
   table path), with truncation and the 500-run cap.
3. Boot crash-recovery requeues interrupted Tier 0/1 jobs and HOLDS Tier 2/3
   (outreach/subscription/polyclaw) for review without auto-running them.
4. misfire_grace_time=300 and coalesce=True on every interval/cron job.
"""

import time

import pytest

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler

import sincor2.scheduler as central
from sincor2 import scheduler_ledger, scheduler_recovery, scheduler_store
from sincor2.scheduler_ledger import SchedulerLedger, wrap_with_ledger


@pytest.fixture(autouse=True)
def _fresh_ledger():
    scheduler_ledger.reset_ledger()
    yield
    scheduler_ledger.reset_ledger()
    central.stop_central_scheduler()


def _quiet_shutdown(sched):
    """Shutdown only if running — non-started schedulers have no threads."""
    try:
        if sched.running:
            sched.shutdown(wait=False)
    except Exception:
        pass


@pytest.fixture()
def canonical_env(monkeypatch):
    """Pin env to canonical defaults so registration is deterministic."""
    env = {
        "DAILY_OPS_ENABLED": "true",
        "SADAS_ENABLED": "true",
        "OUTREACH_ENABLED": "true",
        "CONTENT_AGENT_ENABLED": "true",
        "LAUNCH_OPS_ENABLED": "true",
        "RECURSIVE_OPTIMIZER_ENABLED": "true",
        "SUBSCRIPTION_REMINDER_ENABLED": "true",
        "POLYCLAW_ENABLED": "true",
        "COMPLIANCE_MONITOR_ENABLED": "false",
        "LAUNCH_REVIEW_REMINDER_ENABLED": "false",
        "PARTNER_OUTREACH_ENABLED": "false",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    yield env


# ---------------------------------------------------------------------------
# 1. Job-store factory: fallback behavior
# ---------------------------------------------------------------------------

def test_jobstore_falls_back_to_memory_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    jobstores, backend = scheduler_store.build_jobstore()
    assert backend == "memory"
    assert isinstance(jobstores["default"], MemoryJobStore)
    assert scheduler_store.durable_db_url() is None


def test_jobstore_falls_back_to_memory_on_unreachable_db(monkeypatch):
    # Port 1 refuses immediately — proves "never crash boot" without a 5s wait.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    jobstores, backend = scheduler_store.build_jobstore()
    assert backend == "memory"
    assert isinstance(jobstores["default"], MemoryJobStore)
    assert scheduler_store.durable_db_url() is None


def test_normalize_url_rewrites_heroku_style_scheme():
    assert scheduler_store._normalize_url("postgres://u:p@h/db") == "postgresql://u:p@h/db"
    assert scheduler_store._normalize_url("postgresql://u:p@h/db") == "postgresql://u:p@h/db"


def test_central_scheduler_boots_without_database_url(canonical_env):
    sched = central.start_central_scheduler()
    try:
        assert sched is not None and sched.running
        store = sched._jobstores["default"]
        assert isinstance(store, MemoryJobStore), type(store)
        assert any(j.id == "outreach_cycle" for j in sched.get_jobs())
    finally:
        central.stop_central_scheduler()


def test_central_scheduler_boots_with_unreachable_database_url(canonical_env, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    sched = central.start_central_scheduler()
    try:
        assert sched is not None and sched.running
        store = sched._jobstores["default"]
        assert isinstance(store, MemoryJobStore), type(store)
    finally:
        central.stop_central_scheduler()


def test_durable_jobstore_persists_jobs_sqlite(tmp_path, monkeypatch):
    """SQLAlchemyJobStore round-trip: jobs must be picklable (the dispatcher,
    not a lambda) and survive a scheduler restart against the same DB."""
    db_file = tmp_path / "jobs.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    jobstores, backend = scheduler_store.build_jobstore()
    assert backend == "sqlalchemy"

    # APScheduler persists pending adds on start(); mirror the real boot path.
    sched = BackgroundScheduler(jobstores=jobstores, timezone="America/Chicago")
    registered: list = []
    central._add_job(sched, "sadas_scan", registered)
    sched.start()
    try:
        assert any(j.id == "sadas_scan" for j in sched.get_jobs())
    finally:
        sched.shutdown(wait=True)

    # Fresh scheduler against the same DB file rehydrates the persisted job.
    jobstores2, _ = scheduler_store.build_jobstore()
    sched2 = BackgroundScheduler(jobstores=jobstores2, timezone="America/Chicago")
    sched2.start()
    try:
        assert any(j.id == "sadas_scan" for j in sched2.get_jobs())
    finally:
        sched2.shutdown(wait=True)


# ---------------------------------------------------------------------------
# 2. Run ledger
# ---------------------------------------------------------------------------

def test_ledger_records_ok_run():
    ledger = SchedulerLedger(database_url=None)
    assert ledger.backend_name == "memory"
    wrapped = wrap_with_ledger("daily_ops", lambda: "did 3 things")
    # wrap_with_ledger resolves the singleton; point it at our ledger
    scheduler_ledger._ledger = ledger
    try:
        assert wrapped() == "did 3 things"
    finally:
        scheduler_ledger.reset_ledger()
    runs = ledger.recent_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run["job_id"] == "daily_ops"
    assert run["state"] == "ok"
    assert run["finished_at"] is not None
    assert "did 3 things" in (run["result_summary"] or "")
    assert run["error"] is None


def test_ledger_records_error_run_with_truncation():
    ledger = SchedulerLedger(database_url=None)
    scheduler_ledger._ledger = ledger
    big_msg = "boom-" + "x" * 2000

    def _fails():
        raise ValueError(big_msg)

    wrapped = wrap_with_ledger("sadas_scan", _fails)
    try:
        with pytest.raises(ValueError):
            wrapped()
    finally:
        scheduler_ledger.reset_ledger()
    (run,) = ledger.recent_runs()
    assert run["state"] == "error"
    assert run["finished_at"] is not None
    assert run["error"].startswith("ValueError: boom-")
    assert len(run["error"]) == scheduler_ledger.ERROR_MAX
    assert run["error"].endswith("...")


def test_ledger_result_summary_truncated():
    ledger = SchedulerLedger(database_url=None)
    scheduler_ledger._ledger = ledger
    try:
        wrap_with_ledger("content_cycle", lambda: "y" * 900)()
    finally:
        scheduler_ledger.reset_ledger()
    (run,) = ledger.recent_runs()
    assert run["state"] == "ok"
    assert len(run["result_summary"]) == scheduler_ledger.RESULT_SUMMARY_MAX
    assert run["result_summary"].endswith("...")


def test_ledger_records_timeout_state():
    import time as _time

    ledger = SchedulerLedger(database_url=None)
    scheduler_ledger._ledger = ledger
    try:
        with pytest.raises(TimeoutError):
            wrap_with_ledger("launch_cycle", lambda: _time.sleep(5), timeout_sec=0.05)()
    finally:
        scheduler_ledger.reset_ledger()
    (run,) = ledger.recent_runs()
    assert run["state"] == "timeout"
    assert "timeout_sec=0.05" in (run["error"] or "")


def test_ledger_ring_buffer_caps_at_500():
    ledger = SchedulerLedger(database_url=None)
    for i in range(505):
        run_id = ledger.begin_run("daily_ops")
        ledger.finish_run(run_id, "ok", result_summary=f"run {i}")
    assert ledger.run_count() == 500
    recent = ledger.recent_runs(limit=1000)
    assert len(recent) == 500
    # Oldest evicted: the newest entries survive.
    assert recent[-1]["result_summary"] == "run 504"


def test_ledger_sqlalchemy_backend_path_with_sqlite(tmp_path):
    """The Postgres-table code path (create-if-not-exists, additive)."""
    url = f"sqlite:///{tmp_path}/ledger.db"
    ledger = SchedulerLedger(database_url=url)
    assert ledger.backend_name == "sqlalchemy"
    run_id = ledger.begin_run("daily_ops")
    assert [r["job_id"] for r in ledger.interrupted_runs()] == ["daily_ops"]
    ledger.finish_run(run_id, "ok", result_summary="done")
    assert ledger.interrupted_runs() == []
    assert ledger.run_count() == 1
    # Second init against the same file: additive create-if-not-exists, no crash.
    ledger2 = SchedulerLedger(database_url=url)
    assert ledger2.run_count() == 1


def test_ledger_falls_back_to_memory_on_bad_database_url():
    ledger = SchedulerLedger(database_url="postgresql://u:p@127.0.0.1:1/db")
    assert ledger.backend_name == "memory"
    run_id = ledger.begin_run("daily_ops")
    ledger.finish_run(run_id, "ok")
    assert ledger.run_count() == 1


# ---------------------------------------------------------------------------
# 3. Boot crash-recovery
# ---------------------------------------------------------------------------

def test_crash_recovery_requeues_tier01_and_holds_tier23(canonical_env, caplog):
    ledger = SchedulerLedger(database_url=None)
    # Interrupted (started, never finished) runs across tiers.
    for job_id in ("daily_ops", "sadas_scan"):  # tier 1
        ledger.begin_run(job_id)
    for job_id in ("outreach_cycle", "subscription_reminders"):  # tier 2
        ledger.begin_run(job_id)
    ledger.begin_run("polyclaw_scan")  # tier 3
    ledger.begin_run("compliance_checks")  # tier 1 but DISABLED -> skipped
    # A finished run must not trigger recovery.
    done = ledger.begin_run("content_cycle")
    ledger.finish_run(done, "ok", result_summary="fine")

    sched = BackgroundScheduler(timezone="America/Chicago")
    try:
        with caplog.at_level("WARNING", logger="sincor2.scheduler_recovery"):
            result = scheduler_recovery.run_boot_recovery(sched, ledger=ledger)
    finally:
        _quiet_shutdown(sched)

    assert set(result["requeued"]) == {"daily_ops", "sadas_scan"}
    assert set(result["held"]) == {"outreach_cycle", "subscription_reminders", "polyclaw_scan"}

    job_ids = {j.id for j in sched.get_jobs()}
    assert "daily_ops_recovery" in job_ids
    assert "sadas_scan_recovery" in job_ids
    assert "outreach_cycle_recovery" not in job_ids
    assert "subscription_reminders_recovery" not in job_ids
    assert "polyclaw_scan_recovery" not in job_ids
    assert "compliance_checks_recovery" not in job_ids  # disabled: skipped
    assert "content_cycle_recovery" not in job_ids  # finished: not interrupted

    # Recovery jobs dispatch through the picklable module-level dispatcher.
    rec_job = next(j for j in sched.get_jobs() if j.id == "daily_ops_recovery")
    assert rec_job.func is central._dispatch_job
    assert rec_job.args == ("daily_ops",)

    # Loud HOLD warning naming every held job.
    assert "HELD" in caplog.text
    for job_id in ("outreach_cycle", "subscription_reminders", "polyclaw_scan"):
        assert job_id in caplog.text


def test_crash_recovery_no_interrupted_runs_is_quiet(caplog):
    ledger = SchedulerLedger(database_url=None)
    sched = BackgroundScheduler(timezone="America/Chicago")
    try:
        with caplog.at_level("WARNING", logger="sincor2.scheduler_recovery"):
            result = scheduler_recovery.run_boot_recovery(sched, ledger=ledger)
    finally:
        _quiet_shutdown(sched)
    assert result == {"requeued": [], "held": []}
    assert "HELD" not in caplog.text


def test_crash_recovery_never_raises_on_broken_ledger(caplog):
    class _Broken:
        def interrupted_runs(self):
            raise RuntimeError("db gone")

    sched = BackgroundScheduler(timezone="America/Chicago")
    try:
        result = scheduler_recovery.run_boot_recovery(sched, ledger=_Broken())
    finally:
        _quiet_shutdown(sched)
    assert result == {"requeued": [], "held": []}


def test_recovery_runs_inside_start_central_scheduler(canonical_env):
    """End-to-end: interrupted tier-1 run -> boot requeues it as a date job."""
    ledger = SchedulerLedger(database_url=None)
    ledger.begin_run("sadas_scan")  # interrupted, tier 1, enabled
    ledger.begin_run("outreach_cycle")  # interrupted, tier 2, enabled -> held
    scheduler_ledger._ledger = ledger
    try:
        sched = central.start_central_scheduler()
        assert sched is not None and sched.running
        job_ids = {j.id for j in sched.get_jobs()}
        assert "sadas_scan_recovery" in job_ids
        assert "outreach_cycle_recovery" not in job_ids
        last = scheduler_recovery.get_last_recovery()
        assert "sadas_scan" in last["requeued"]
        assert "outreach_cycle" in last["held"]
    finally:
        scheduler_ledger.reset_ledger()
        central.stop_central_scheduler()


# ---------------------------------------------------------------------------
# 4. misfire_grace_time + coalesce on all jobs
# ---------------------------------------------------------------------------

def test_misfire_grace_time_and_coalesce_on_all_jobs(canonical_env):
    sched = central.start_central_scheduler()
    try:
        jobs = [j for j in sched.get_jobs() if not j.id.endswith("_startup")]
        assert jobs, "expected recurring jobs to be registered"
        for job in jobs:
            assert job.coalesce is True, f"{job.id}: coalesce={job.coalesce}"
            assert job.misfire_grace_time == 300, (
                f"{job.id}: misfire_grace_time={job.misfire_grace_time}"
            )
        # The canonical enabled set from STREAM A, plus the AXM activity
        # engine (registered on the central registry post-consolidation).
        ids = {j.id for j in jobs}
        assert ids == {
            "daily_ops", "sadas_scan", "outreach_cycle", "content_cycle",
            "launch_cycle", "recursive_optimizer", "subscription_reminders",
            "polyclaw_scan", "activity_engine_cycle",
        }
    finally:
        central.stop_central_scheduler()


def test_dispatch_job_records_ledger_run(canonical_env):
    """_dispatch_job keeps the _safe_run contract and writes a ledger run."""
    central._dispatch_job("recursive_optimizer")
    ledger = scheduler_ledger.get_ledger()
    runs = [r for r in ledger.recent_runs() if r["job_id"] == "recursive_optimizer"]
    assert runs, "expected a ledger run for recursive_optimizer"
    assert runs[-1]["state"] in ("ok", "error")  # whatever the job did, it was recorded
