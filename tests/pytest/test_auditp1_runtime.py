"""P1 audit remediation regression tests — Batch C (Runtime fixes).

Finding #9: scheduler side-effect defaults.
  - Outreach/content send/publish jobs default OFF (fail closed).
  - Sends require explicit env opt-in + approved-recipients allowlist.
  - SINCOR_COMMS_KILL_SWITCH blocks every send (checked live).

Finding #10: agency_kernel_tools hardening.
  - python_exec requires explicit founder approval (env flag).
  - file_read restricted to the agent sandbox; repo root and /tmp denied;
    symlink/.. escapes denied after realpath re-check.
  - Secret-shaped values redacted from tool outputs.

Env vars used here are set/cleared per-test with monkeypatch so ambient
environment (CI, dev machines) cannot change the outcome.
"""

import os

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GATING_ENV = (
    "OUTREACH_ENABLED",
    "CONTENT_AGENT_ENABLED",
    "OUTREACH_APPROVED_RECIPIENTS",
    "SINCOR_COMMS_KILL_SWITCH",
    "AUTONOMOUS_AGENTS",
    "SINCOR_AGENT_EXEC_APPROVED",
    "SINCOR_AGENT_SANDBOX_DIR",
)


@pytest.fixture
def clean_gating_env(monkeypatch):
    """Clear all gating env vars so every test starts from fail-closed defaults."""
    for var in _GATING_ENV:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


class _FakeResendEmails:
    def __init__(self):
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)
        return {"id": "fake-msg-1"}


class _FakeResend:
    def __init__(self):
        self.emails = _FakeResendEmails()


# ===========================================================================
# Finding #9 — comms_gating unit behavior
# ===========================================================================

class TestCommsGating:
    def test_default_fail_closed(self, clean_gating_env):
        from sincor2.comms_gating import (
            kill_switch_engaged, outreach_opted_in, content_opted_in,
            outreach_send_allowed, content_publish_allowed,
        )
        assert not kill_switch_engaged()
        assert not outreach_opted_in()
        assert not content_opted_in()
        assert outreach_send_allowed("ops@example.com") == (False, "outreach_not_opted_in")
        assert content_publish_allowed() == (False, "content_not_opted_in")

    def test_opt_in_truthy_variants(self, clean_gating_env):
        from sincor2 import comms_gating
        for val in ("true", "TRUE", "1", "yes", "  true  "):
            clean_gating_env.setenv("OUTREACH_ENABLED", val)
            assert comms_gating.outreach_opted_in(), val
            clean_gating_env.setenv("CONTENT_AGENT_ENABLED", val)
            assert comms_gating.content_opted_in(), val

    def test_opt_in_rejects_lookalikes(self, clean_gating_env):
        from sincor2 import comms_gating
        for val in ("false", "0", "no", "yesplease", "truee", "enabled", "on "):
            clean_gating_env.setenv("OUTREACH_ENABLED", val)
            assert not comms_gating.outreach_opted_in(), val

    def test_autonomous_agents_false_overrides(self, clean_gating_env):
        from sincor2 import comms_gating
        clean_gating_env.setenv("OUTREACH_ENABLED", "true")
        clean_gating_env.setenv("AUTONOMOUS_AGENTS", "false")
        assert not comms_gating.outreach_opted_in()

    def test_kill_switch_variants(self, clean_gating_env):
        from sincor2 import comms_gating
        for val in ("1", "true", "TRUE", "yes", " 1 "):
            clean_gating_env.setenv("SINCOR_COMMS_KILL_SWITCH", val)
            assert comms_gating.kill_switch_engaged(), val
        for val in ("0", "false", "no", ""):
            clean_gating_env.setenv("SINCOR_COMMS_KILL_SWITCH", val)
            assert not comms_gating.kill_switch_engaged(), val

    def test_kill_switch_checked_live_not_cached(self, clean_gating_env):
        from sincor2 import comms_gating
        clean_gating_env.setenv("OUTREACH_ENABLED", "true")
        clean_gating_env.setenv("OUTREACH_APPROVED_RECIPIENTS", "ops@example.com")
        assert comms_gating.outreach_send_allowed("ops@example.com") == (True, "ok")
        # Flip the kill switch at runtime — no restart, no re-import.
        clean_gating_env.setenv("SINCOR_COMMS_KILL_SWITCH", "1")
        ok, reason = comms_gating.outreach_send_allowed("ops@example.com")
        assert (ok, reason) == (False, "kill_switch_engaged")
        clean_gating_env.delenv("SINCOR_COMMS_KILL_SWITCH")
        assert comms_gating.outreach_send_allowed("ops@example.com") == (True, "ok")

    def test_allowlist_required_and_enforced(self, clean_gating_env):
        from sincor2 import comms_gating
        clean_gating_env.setenv("OUTREACH_ENABLED", "true")
        # Opt-in but no recipients configured -> never send.
        assert comms_gating.outreach_send_allowed("ops@example.com") == (False, "no_approved_recipients")
        clean_gating_env.setenv("OUTREACH_APPROVED_RECIPIENTS", "ops@example.com, Founder@Example.com")
        assert comms_gating.outreach_send_allowed("ops@example.com") == (True, "ok")
        assert comms_gating.outreach_send_allowed("founder@example.com") == (True, "ok")  # case-insensitive
        assert comms_gating.outreach_send_allowed("stranger@example.com") == (False, "recipient_not_allowlisted")


# ===========================================================================
# Finding #9 — outreach engine + scheduler wiring
# ===========================================================================

class TestOutreachWiring:
    def test_engine_disabled_by_default(self, clean_gating_env):
        from sincor2.outreach_engine import OutreachEngine, _autonomous_on
        assert not _autonomous_on()
        engine = OutreachEngine()
        assert engine.enabled is False
        assert engine.run_cycle()["status"] == "disabled"

    def test_engine_send_refused_by_default(self, clean_gating_env):
        from sincor2.outreach_engine import OutreachEngine
        engine = OutreachEngine()
        fake = _FakeResend()
        assert engine.send_outreach_email({"email": "ops@example.com", "name": "X"}, fake) is False
        assert fake.emails.sent == []

    def test_engine_optin_without_recipients_never_sends(self, clean_gating_env):
        clean_gating_env.setenv("OUTREACH_ENABLED", "true")
        from sincor2.outreach_engine import OutreachEngine
        engine = OutreachEngine()
        assert engine.enabled is True  # opted in...
        fake = _FakeResend()
        assert engine.send_outreach_email({"email": "ops@example.com", "name": "X"}, fake) is False
        assert fake.emails.sent == []  # ...but no recipients configured -> no send

    def test_engine_allowlisted_recipient_sends(self, clean_gating_env):
        clean_gating_env.setenv("OUTREACH_ENABLED", "true")
        clean_gating_env.setenv("OUTREACH_APPROVED_RECIPIENTS", "ops@example.com")
        from sincor2.outreach_engine import OutreachEngine
        engine = OutreachEngine()
        fake = _FakeResend()
        assert engine.send_outreach_email({"email": "ops@example.com", "name": "X"}, fake) is True
        assert len(fake.emails.sent) == 1
        assert fake.emails.sent[0]["to"] == "ops@example.com"

    def test_engine_non_allowlisted_recipient_refused(self, clean_gating_env):
        clean_gating_env.setenv("OUTREACH_ENABLED", "true")
        clean_gating_env.setenv("OUTREACH_APPROVED_RECIPIENTS", "ops@example.com")
        from sincor2.outreach_engine import OutreachEngine
        engine = OutreachEngine()
        fake = _FakeResend()
        assert engine.send_outreach_email({"email": "attacker@evil.example", "name": "X"}, fake) is False
        assert fake.emails.sent == []

    def test_engine_kill_switch_blocks_send(self, clean_gating_env):
        clean_gating_env.setenv("OUTREACH_ENABLED", "true")
        clean_gating_env.setenv("OUTREACH_APPROVED_RECIPIENTS", "ops@example.com")
        clean_gating_env.setenv("SINCOR_COMMS_KILL_SWITCH", "1")
        from sincor2.outreach_engine import OutreachEngine
        engine = OutreachEngine()
        fake = _FakeResend()
        assert engine.send_outreach_email({"email": "ops@example.com", "name": "X"}, fake) is False
        assert fake.emails.sent == []

    def test_stale_engine_cannot_send_after_optin_revoked(self, clean_gating_env):
        # Engine constructed while opted in, then the founder revokes opt-in.
        # The per-send backstop re-checks gates live, so the stale engine
        # must still refuse.
        clean_gating_env.setenv("OUTREACH_ENABLED", "true")
        clean_gating_env.setenv("OUTREACH_APPROVED_RECIPIENTS", "ops@example.com")
        from sincor2.outreach_engine import OutreachEngine
        engine = OutreachEngine()
        assert engine.enabled is True
        clean_gating_env.delenv("OUTREACH_ENABLED")
        fake = _FakeResend()
        assert engine.send_outreach_email({"email": "ops@example.com", "name": "X"}, fake) is False
        assert fake.emails.sent == []

    def test_outreach_scheduler_default_off(self, clean_gating_env):
        import sincor2.outreach_scheduler as sched
        sched._scheduler = None
        assert sched.is_outreach_explicitly_enabled() is False
        assert sched.start_outreach_scheduler() is None

    def test_outreach_scheduler_starts_when_opted_in(self, clean_gating_env):
        clean_gating_env.setenv("OUTREACH_ENABLED", "true")
        import sincor2.outreach_scheduler as sched
        sched._scheduler = None
        try:
            assert sched.is_outreach_explicitly_enabled() is True
            running = sched.start_outreach_scheduler()
            assert running is not None
        finally:
            sched.stop_outreach_scheduler()
            sched._scheduler = None


class _StubSched:
    """Minimal stand-in for BackgroundScheduler: records registered job ids."""
    def __init__(self):
        self.jobs = []

    def add_job(self, func, **kwargs):
        self.jobs.append(kwargs.get("id"))


class TestCentralSchedulerDefaults:
    def test_send_publish_jobs_default_off(self, clean_gating_env):
        from sincor2 import scheduler
        stub = _StubSched()
        registered = scheduler._register_jobs(stub)
        assert "outreach_cycle" not in registered
        assert "content_agent" not in registered
        assert "launch_content_cycle" not in registered
        assert "outreach_cycle" not in stub.jobs

    def test_send_publish_jobs_register_when_opted_in(self, clean_gating_env):
        clean_gating_env.setenv("OUTREACH_ENABLED", "true")
        clean_gating_env.setenv("CONTENT_AGENT_ENABLED", "true")
        clean_gating_env.setenv("LAUNCH_OPS_ENABLED", "true")
        from sincor2 import scheduler
        stub = _StubSched()
        registered = scheduler._register_jobs(stub)
        assert "outreach_cycle" in registered
        assert "content_agent" in registered
        assert "launch_content_cycle" in registered

    def test_partial_opt_in_registers_only_opted_in(self, clean_gating_env):
        clean_gating_env.setenv("OUTREACH_ENABLED", "1")
        from sincor2 import scheduler
        stub = _StubSched()
        registered = scheduler._register_jobs(stub)
        assert "outreach_cycle" in registered
        assert "content_agent" not in registered
        assert "launch_content_cycle" not in registered


# ===========================================================================
# Finding #9 — content publish gating
# ===========================================================================

class TestContentPublishGating:
    def _make_wp(self, clean_gating_env):
        clean_gating_env.setenv("WP_API_URL", "https://example.invalid")
        clean_gating_env.setenv("WP_USERNAME", "u")
        clean_gating_env.setenv("WP_APP_PASSWORD", "p")
        from sincor2.content_agent import WordPressPublisher
        return WordPressPublisher()

    def _post(self):
        return {"title": "T", "slug": "t", "markdown": "# T", "meta_description": "", "keyword": "k"}

    def test_publish_refused_without_optin(self, clean_gating_env):
        wp = self._make_wp(clean_gating_env)
        result = wp.publish(self._post())
        assert result["error"] == "publish_blocked"
        assert result["reason"] == "content_not_opted_in"

    def test_publish_refused_on_kill_switch_even_when_opted_in(self, clean_gating_env):
        clean_gating_env.setenv("CONTENT_AGENT_ENABLED", "true")
        clean_gating_env.setenv("SINCOR_COMMS_KILL_SWITCH", "true")
        wp = self._make_wp(clean_gating_env)
        result = wp.publish(self._post())
        assert result["error"] == "publish_blocked"
        assert result["reason"] == "kill_switch_engaged"

    def test_publish_refused_on_kill_switch_for_manual_publish(self, clean_gating_env):
        # manual=True waives the scheduler opt-in but NEVER the kill switch.
        clean_gating_env.setenv("SINCOR_COMMS_KILL_SWITCH", "1")
        wp = self._make_wp(clean_gating_env)
        result = wp.publish(self._post(), manual=True)
        assert result["error"] == "publish_blocked"
        assert result["reason"] == "kill_switch_engaged"

    def test_publish_proceeds_when_opted_in(self, clean_gating_env, monkeypatch):
        clean_gating_env.setenv("CONTENT_AGENT_ENABLED", "true")
        wp = self._make_wp(clean_gating_env)
        calls = []

        class _Resp:
            def read(self):
                return b'{"id": 42, "link": "https://example.invalid/t"}'
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=30: (calls.append(req), _Resp())[1])
        import sincor2.content_agent as ca
        monkeypatch.setattr(ca, "get_db", lambda: _FakeDb())
        result = wp.publish(self._post())
        assert result.get("wp_post_id") == 42
        assert len(calls) == 1

    def test_content_scheduler_default_off(self, clean_gating_env):
        import sincor2.content_scheduler as sched
        sched._scheduler = None
        assert sched.start_content_scheduler() is None


class _FakeDb:
    """get_db() stand-in: context manager with an execute() that records."""
    def __init__(self):
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        return self


# ===========================================================================
# Finding #10 — agency_kernel_tools hardening
# ===========================================================================

class TestPythonExecGate:
    def test_exec_denied_without_approval(self, clean_gating_env):
        from sincor2.agency_kernel_tools import tool_python_exec
        result = tool_python_exec("result = 1 + 1")
        assert result["status"] == "failed"
        assert "SINCOR_AGENT_EXEC_APPROVED" in result["error"]

    def test_exec_denied_with_falsy_flag(self, clean_gating_env):
        clean_gating_env.setenv("SINCOR_AGENT_EXEC_APPROVED", "false")
        from sincor2.agency_kernel_tools import tool_python_exec
        result = tool_python_exec("result = 1 + 1")
        assert result["status"] == "failed"

    def test_exec_works_with_founder_approval(self, clean_gating_env):
        clean_gating_env.setenv("SINCOR_AGENT_EXEC_APPROVED", "true")
        from sincor2.agency_kernel_tools import tool_python_exec
        result = tool_python_exec("result = 2 + 2")
        assert result["status"] == "success"
        assert result["outputs"]["result"] == 4

    def test_exec_banned_constructs_still_rejected_with_approval(self, clean_gating_env):
        clean_gating_env.setenv("SINCOR_AGENT_EXEC_APPROVED", "1")
        from sincor2.agency_kernel_tools import tool_python_exec
        for code in ("import os", "x = __import__('os')", "open('/etc/passwd')", "eval('1')"):
            result = tool_python_exec(code)
            assert result["status"] == "failed", code
            assert "Banned construct" in result["error"], code

    def test_exec_gate_checked_live(self, clean_gating_env):
        # Approval granted then revoked mid-process: the later call must fail.
        from sincor2.agency_kernel_tools import tool_python_exec
        clean_gating_env.setenv("SINCOR_AGENT_EXEC_APPROVED", "true")
        assert tool_python_exec("result = 1")["status"] == "success"
        clean_gating_env.delenv("SINCOR_AGENT_EXEC_APPROVED")
        assert tool_python_exec("result = 1")["status"] == "failed"


class TestFileReadSandbox:
    def test_read_inside_sandbox_works(self, clean_gating_env, tmp_path):
        clean_gating_env.setenv("SINCOR_AGENT_SANDBOX_DIR", str(tmp_path))
        (tmp_path / "notes.txt").write_text("hello agent")
        from sincor2.agency_kernel_tools import tool_file_read
        result = tool_file_read("notes.txt")
        assert result["status"] == "success"
        assert result["content"] == "hello agent"

    def test_read_absolute_inside_sandbox_works(self, clean_gating_env, tmp_path):
        clean_gating_env.setenv("SINCOR_AGENT_SANDBOX_DIR", str(tmp_path))
        target = tmp_path / "abs.txt"
        target.write_text("absolute ok")
        from sincor2.agency_kernel_tools import tool_file_read
        assert tool_file_read(str(target))["status"] == "success"

    def test_read_outside_sandbox_denied(self, clean_gating_env, tmp_path):
        clean_gating_env.setenv("SINCOR_AGENT_SANDBOX_DIR", str(tmp_path))
        from sincor2.agency_kernel_tools import tool_file_read
        # /tmp is outside the sandbox now (was an allowed root before the fix)
        probe = tmp_path.parent / "outside_probe.txt"
        probe.write_text("nope")
        try:
            result = tool_file_read(str(probe))
            assert result["status"] == "failed"
            assert "sandbox" in result["error"]
            # repo root / source tree is outside the sandbox too
            repo_file = os.path.abspath(__file__)
            result = tool_file_read(repo_file)
            assert result["status"] == "failed"
        finally:
            probe.unlink(missing_ok=True)

    def test_dotdot_traversal_denied(self, clean_gating_env, tmp_path):
        clean_gating_env.setenv("SINCOR_AGENT_SANDBOX_DIR", str(tmp_path))
        (tmp_path / "sub").mkdir()
        secret = tmp_path.parent / "traversal_secret.txt"
        secret.write_text("traversed")
        try:
            from sincor2.agency_kernel_tools import tool_file_read
            assert tool_file_read("sub/../../traversal_secret.txt")["status"] == "failed"
            assert tool_file_read(str(tmp_path / ".." / "traversal_secret.txt"))["status"] == "failed"
        finally:
            secret.unlink(missing_ok=True)

    def test_symlink_escape_denied(self, clean_gating_env, tmp_path):
        clean_gating_env.setenv("SINCOR_AGENT_SANDBOX_DIR", str(tmp_path))
        outside = tmp_path.parent / "symlink_target.txt"
        outside.write_text("secret-data")
        link = tmp_path / "evil_link"
        link.symlink_to(outside)
        try:
            from sincor2.agency_kernel_tools import tool_file_read
            result = tool_file_read("evil_link")
            assert result["status"] == "failed"
            assert "sandbox" in result["error"]
        finally:
            link.unlink(missing_ok=True)
            outside.unlink(missing_ok=True)

    def test_symlink_chain_escape_denied(self, clean_gating_env, tmp_path):
        clean_gating_env.setenv("SINCOR_AGENT_SANDBOX_DIR", str(tmp_path))
        outside = tmp_path.parent / "chain_target.txt"
        outside.write_text("chain-secret")
        link1 = tmp_path / "link1"
        link2 = tmp_path / "link2"
        link2.symlink_to(outside)
        link1.symlink_to(link2)
        try:
            from sincor2.agency_kernel_tools import tool_file_read
            assert tool_file_read("link1")["status"] == "failed"
            assert tool_file_read("link2")["status"] == "failed"
        finally:
            link1.unlink(missing_ok=True)
            link2.unlink(missing_ok=True)
            outside.unlink(missing_ok=True)

    def test_symlink_inside_sandbox_allowed(self, clean_gating_env, tmp_path):
        # A symlink that stays inside the sandbox is legitimate.
        clean_gating_env.setenv("SINCOR_AGENT_SANDBOX_DIR", str(tmp_path))
        real = tmp_path / "real.txt"
        real.write_text("inner")
        link = tmp_path / "inner_link"
        link.symlink_to(real)
        from sincor2.agency_kernel_tools import tool_file_read
        result = tool_file_read("inner_link")
        assert result["status"] == "success"
        assert result["content"] == "inner"

    def test_default_sandbox_is_repo_data_dir(self, clean_gating_env):
        from sincor2.agency_kernel_tools import _sandbox_root
        root = _sandbox_root()
        assert root.name == "agent_sandbox"
        assert root.parent.name == "data"


class TestSecretRedaction:
    _SECRETS = {
        "stripe": "sk_test_abc123DEF456ghi789jkl",
        "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlMTIzNDU2Nzg5MA",
        "aws": "AKIAIOSFODNN7EXAMPLE",
        "github": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234",
        "resend": "re_1234567890ABCDEFGHIJKLmnop",
    }

    def _secret_blob(self):
        lines = [f"{k}={v}" for k, v in self._SECRETS.items()]
        lines.append("-----BEGIN RSA PRIVATE KEY-----")
        lines.append("MIIEpAIBAAKCAQEA7b8fakesecretkeymaterial")
        lines.append("-----END RSA PRIVATE KEY-----")
        return "\n".join(lines)

    def test_file_read_redacts_secrets(self, clean_gating_env, tmp_path):
        clean_gating_env.setenv("SINCOR_AGENT_SANDBOX_DIR", str(tmp_path))
        blob = self._secret_blob()
        (tmp_path / "leak.txt").write_text(blob)
        from sincor2.agency_kernel_tools import tool_file_read
        result = tool_file_read("leak.txt")
        assert result["status"] == "success"
        for raw in list(self._SECRETS.values()) + ["MIIEpAIBAAKCAQEA7b8fakesecretkeymaterial"]:
            assert raw not in result["content"], raw[:12]
        assert "REDACTED" in result["content"]

    def test_python_exec_output_redacted(self, clean_gating_env):
        clean_gating_env.setenv("SINCOR_AGENT_EXEC_APPROVED", "true")
        from sincor2.agency_kernel_tools import tool_python_exec
        result = tool_python_exec("result = 'key=sk_test_abc123DEF456ghi789jkl'")
        assert result["status"] == "success"
        assert "sk_test_abc123DEF456ghi789jkl" not in str(result["outputs"])
        assert "REDACTED" in str(result["outputs"])

    def test_run_tools_for_step_final_sweep(self, clean_gating_env, tmp_path):
        clean_gating_env.setenv("SINCOR_AGENT_SANDBOX_DIR", str(tmp_path))
        (tmp_path / "sweep.txt").write_text("token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234")
        from sincor2.agency_kernel_tools import run_tools_for_step
        result = run_tools_for_step(
            tools_required=["file_read"],
            step_description="read a file",
            step_inputs={"path": "sweep.txt"},
        )
        assert result["status"] == "success"
        assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234" not in str(result["outputs"])
        assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234" not in str(result["evidence"])

    def test_redaction_does_not_mangle_benign_text(self, clean_gating_env):
        from sincor2.agency_kernel_tools import _redact_text
        benign = "The task is to review docs v1.2.3 and email ops@example.com about it."
        assert _redact_text(benign) == benign
