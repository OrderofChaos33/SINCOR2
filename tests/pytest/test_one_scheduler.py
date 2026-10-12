"""One-scheduler consolidation tests (STREAM A, 2026-10-11).

Verifies:
1. The central scheduler registers exactly ONE outreach job (the 2x
   double-fire fix) plus the full canonical job set from SCHEDULER_DEFAULTS.
2. SCHEDULER_DEFAULTS matches the founder-decided canonical defaults and
   every job carries max_instances=1.
3. The 8 standalone *_scheduler modules no longer create APScheduler
   instances (no start_*_scheduler functions, no BackgroundScheduler usage).
"""

import importlib
from pathlib import Path

import pytest

import sincor2.scheduler as central


CANONICAL_MODULES = [
    "sincor2.outreach_scheduler",
    "sincor2.content_scheduler",
    "sincor2.launch_ops_scheduler",
    "sincor2.launch_review_notify",
    "sincor2.partner_outreach_notify",
    "sincor2.compliance_monitor",
    "sincor2.subscription_scheduler",
    "sincor2.polyclaw_scheduler",
]


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
    yield env
    central.stop_central_scheduler()


def _boot():
    central.stop_central_scheduler()
    sched = central.start_central_scheduler()
    assert sched is not None and sched.running
    return sched


def test_scheduler_defaults_match_founder_decisions():
    expected_enabled = {
        "daily_ops": True,
        "sadas_scan": True,
        "outreach_cycle": True,
        "content_cycle": True,
        "launch_cycle": True,
        "recursive_optimizer": True,
        "subscription_reminders": True,
        "polyclaw_scan": True,
        "compliance_checks": False,
        "launch_review_notify": False,
        "partner_notify": False,
    }
    assert set(central.SCHEDULER_DEFAULTS) == set(expected_enabled)
    for job_id, enabled in expected_enabled.items():
        spec = central.SCHEDULER_DEFAULTS[job_id]
        assert spec["enabled"] is enabled, job_id
        assert spec["max_instances"] == 1, job_id
        assert spec["env_enabled"], job_id


def test_exactly_one_outreach_job_registered(canonical_env):
    sched = _boot()
    try:
        jobs = sched.get_jobs()
        outreach = [j for j in jobs if j.id == "outreach_cycle"]
        assert len(outreach) == 1, (
            f"double-fire regression: found {len(outreach)} outreach_cycle jobs"
        )
        ids = sorted(j.id for j in jobs)
        # recurring canonical jobs (startup one-shots excluded from this check)
        for job_id in (
            "daily_ops", "sadas_scan", "outreach_cycle", "content_cycle",
            "launch_cycle", "recursive_optimizer", "subscription_reminders",
            "polyclaw_scan",
        ):
            assert ids.count(job_id) == 1, f"{job_id}: count={ids.count(job_id)}"
        # OFF-by-default jobs must NOT be registered
        for job_id in ("compliance_checks", "launch_review_notify", "partner_notify"):
            assert job_id not in ids, f"{job_id} unexpectedly registered"
    finally:
        central.stop_central_scheduler()


def test_all_jobs_have_max_instances_1(canonical_env):
    sched = _boot()
    try:
        for job in sched.get_jobs():
            if job.id.endswith("_startup"):
                continue
            assert job.max_instances == 1, f"{job.id}: max_instances={job.max_instances}"
    finally:
        central.stop_central_scheduler()


def test_standalone_modules_create_no_schedulers():
    removed_starters = {
        "sincor2.outreach_scheduler": "start_outreach_scheduler",
        "sincor2.content_scheduler": "start_content_scheduler",
        "sincor2.launch_ops_scheduler": "start_launch_ops_scheduler",
        "sincor2.launch_review_notify": "start_review_reminder_scheduler",
        "sincor2.partner_outreach_notify": "start_partner_reminder_scheduler",
        "sincor2.compliance_monitor": "start_compliance_scheduler",
        "sincor2.subscription_scheduler": "start_subscription_scheduler",
        "sincor2.polyclaw_scheduler": "start_polyclaw_scheduler",
    }
    for mod_name, starter in removed_starters.items():
        mod = importlib.import_module(mod_name)
        assert not hasattr(mod, starter), f"{mod_name}.{starter} still exists"
        assert not hasattr(mod, "_scheduler"), f"{mod_name} still has _scheduler global"
        source = Path(mod.__file__).read_text()
        assert "BackgroundScheduler(" not in source, f"{mod_name} still builds a scheduler"


def test_job_function_libraries_intact():
    """The standalone modules keep their job functions; central uses them."""
    from sincor2 import outreach_scheduler, content_scheduler, launch_ops_scheduler
    from sincor2 import launch_review_notify, partner_outreach_notify
    from sincor2 import subscription_scheduler, polyclaw_scheduler, compliance_monitor

    assert callable(outreach_scheduler.run_outreach_cycle)
    assert callable(content_scheduler.run_content_cycle)
    assert callable(launch_ops_scheduler._run_content_cycle)
    assert callable(launch_review_notify.send_launch_review_reminder)
    assert callable(partner_outreach_notify.send_partner_outreach_reminder)
    assert callable(subscription_scheduler._send_renewal_reminders)
    assert callable(polyclaw_scheduler.run_polyclaw_cycle)
    assert callable(compliance_monitor.run_all_checks)
    # polyclaw live/dry-run posture untouched: defaults still dry-run
    import os
    assert os.environ.get("POLYCLAW_LIVE", "false").lower() == "false"


def test_status_reports_canonical_jobs(canonical_env):
    _boot()
    try:
        status = central.get_scheduler_status()
        assert status["running"] is True
        ids = {j["id"] for j in status["jobs"]}
        assert "outreach_cycle" in ids
        assert "polyclaw_scan" in ids
        assert "subscription_reminders" in ids
    finally:
        central.stop_central_scheduler()
