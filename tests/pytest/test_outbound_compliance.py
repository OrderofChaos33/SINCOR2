"""Wren PR 8 — outbound email compliance plumbing.

Covers the non-blocked parts of the compliance directive:

  - config/railway_env.example ships every send/publish/trading flag OFF
    (the tracked railway_add_these.env armed them all).
  - OUTREACH_ENABLED truthy + empty allowlist -> CRITICAL log, sends refused.
  - Suppression list: a signed one-click unsubscribe endpoint (GET+POST)
    adds the address; every send path then refuses it. Tampered tokens
    fail closed.
  - Outreach sends carry List-Unsubscribe / List-Unsubscribe-Post headers
    and a physical postal-address footer from env; a send REFUSES when
    the postal-address env is unset.
  - Per-domain cap (3/day) and the global daily cap are read live at
    send time; the kill switch is honored live.
  - Copy points at the USDC pack rail (/pricing), not "SINC monthly or
    AXM one-time" / /buy.
  - SPF/DKIM/DMARC presence check never raises (logged, not fatal).

Env is set/cleared per-test with monkeypatch; the compliance store lives
under a tmp SINCOR_DATA_DIR so tests never touch the repo data dir.
"""

import logging
import os
from pathlib import Path

import pytest

_ENV_VARS = (
    "OUTREACH_ENABLED",
    "AUTONOMOUS_AGENTS",
    "CONTENT_AGENT_ENABLED",
    "PARTNER_OUTREACH_ENABLED",
    "POLYCLAW_ENABLED",
    "TREASURY_CONVERSION_ENABLED",
    "OUTREACH_APPROVED_RECIPIENTS",
    "SINCOR_COMMS_KILL_SWITCH",
    "SINCOR_POSTAL_ADDRESS",
    "UNSUBSCRIBE_TOKEN_SECRET",
    "OUTREACH_DAILY_LIMIT",
    "OUTREACH_PER_DOMAIN_DAILY_LIMIT",
    "SINCOR_PUBLIC_BASE_URL",
    "RESEND_API_KEY",
    "YELP_API_KEY",
)

_POSTAL = "SINCOR Business Solutions, 715 Park Pl, Clinton, IA 52732"
_EXAMPLE_PATH = Path(__file__).resolve().parents[2] / "config" / "railway_env.example"
_OLD_PATH = Path(__file__).resolve().parents[2] / "config" / "railway_add_these.env"


@pytest.fixture
def compliance_env(monkeypatch, tmp_path):
    """Fail-closed baseline: every compliance env var cleared, tmp data dir."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNSUBSCRIBE_TOKEN_SECRET", "test-unsub-secret-0123456789abcdef")
    return monkeypatch


@pytest.fixture
def armed_env(compliance_env):
    """Fully armed + compliant config: opt-in, allowlist, postal address."""
    compliance_env.setenv("OUTREACH_ENABLED", "true")
    compliance_env.setenv(
        "OUTREACH_APPROVED_RECIPIENTS",
        "ops@example.com, a@foo.example, b@foo.example, c@foo.example, "
        "d@foo.example, other@bar.example",
    )
    compliance_env.setenv("SINCOR_POSTAL_ADDRESS", _POSTAL)
    return compliance_env


class _FakeResendEmails:
    def __init__(self):
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)
        return {"id": "fake-msg-1"}


class _FakeResend:
    def __init__(self):
        self.emails = _FakeResendEmails()


def _parse_env_file(path):
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values


# ===========================================================================
# 1. Example env file — every send/publish/trading flag OFF
# ===========================================================================

class TestExampleEnvFile:
    SEND_FLAGS = (
        "OUTREACH_ENABLED",
        "AUTONOMOUS_AGENTS",
        "CONTENT_AGENT_ENABLED",
        "PARTNER_OUTREACH_ENABLED",
        "POLYCLAW_ENABLED",
        "TREASURY_CONVERSION_ENABLED",
    )

    def test_old_armed_file_is_gone(self):
        assert not _OLD_PATH.exists(), "railway_add_these.env must be renamed away"
        assert _EXAMPLE_PATH.exists(), "config/railway_env.example must exist"

    def test_send_flags_default_off(self):
        values = _parse_env_file(_EXAMPLE_PATH)
        for flag in self.SEND_FLAGS:
            assert flag in values, f"{flag} missing from example env"
            assert values[flag].lower() in ("false", "0", "no", ""), (
                f"{flag} must default OFF in the example env, got {values[flag]!r}"
            )

    def test_example_values_never_allow_a_send(self, compliance_env):
        for key, val in _parse_env_file(_EXAMPLE_PATH).items():
            if val:
                compliance_env.setenv(key, val)
            else:
                compliance_env.delenv(key, raising=False)
        from sincor2.comms_gating import outreach_send_allowed
        ok, _reason = outreach_send_allowed("anyone@example.com")
        assert ok is False


# ===========================================================================
# 2. Armed-but-no-allowlist guard: CRITICAL log + refusal
# ===========================================================================

class TestArmedWithoutAllowlistGuard:
    def test_critical_log_and_refusal(self, compliance_env, caplog):
        compliance_env.setenv("OUTREACH_ENABLED", "true")
        from sincor2.comms_gating import outreach_send_allowed
        with caplog.at_level(logging.CRITICAL, logger="sincor2.comms_gating"):
            ok, reason = outreach_send_allowed("ops@example.com")
        assert (ok, reason) == (False, "no_approved_recipients")
        assert any(
            rec.levelno == logging.CRITICAL and "OUTREACH_APPROVED_RECIPIENTS" in rec.message
            for rec in caplog.records
        ), "expected a CRITICAL log naming the empty allowlist"


# ===========================================================================
# 3. Postal address: refusal when unset, footer when set
# ===========================================================================

class TestPostalAddress:
    def test_cycle_refused_without_postal_address(self, armed_env):
        # The refusal sits in run_cycle() — the only production caller of
        # send_outreach_email — because the pre-existing finding-#9 pin
        # (test_engine_allowlisted_recipient_sends) encodes the
        # pre-CAN-SPAM direct-call contract and may not be edited by a
        # builder. A send-path refusal WAS built first and evidenced: it
        # fired exactly as designed and broke only that one pin. Moving
        # it back into send_outreach_email is a one-block change gated on
        # a founder ruling to update that pin (see PR 8 report).
        armed_env.delenv("SINCOR_POSTAL_ADDRESS", raising=False)
        armed_env.setenv("RESEND_API_KEY", "re_test")
        armed_env.setenv("YELP_API_KEY", "yelp_test")
        from sincor2.outreach_engine import OutreachEngine
        engine = OutreachEngine()
        assert engine.enabled is True
        summary = engine.run_cycle()
        assert summary["status"] == "no_postal_address"
        assert summary["sent"] == 0

    def test_send_carries_postal_footer(self, armed_env):
        from sincor2.outreach_engine import OutreachEngine
        engine = OutreachEngine()
        fake = _FakeResend()
        assert engine.send_outreach_email(
            {"email": "ops@example.com", "name": "Ops"}, fake
        ) is True
        payload = fake.emails.sent[0]
        assert _POSTAL in payload["html"]
        assert _POSTAL in payload["text"]


# ===========================================================================
# 4. List-Unsubscribe headers + signed one-click unsubscribe + suppression
# ===========================================================================

class TestUnsubscribe:
    def test_message_carries_list_unsubscribe_headers(self, armed_env):
        from sincor2.outreach_engine import OutreachEngine
        engine = OutreachEngine()
        fake = _FakeResend()
        assert engine.send_outreach_email(
            {"email": "ops@example.com", "name": "Ops"}, fake
        ) is True
        headers = fake.emails.sent[0].get("headers") or {}
        assert "/unsubscribe/" in headers.get("List-Unsubscribe", "")
        assert headers.get("List-Unsubscribe-Post") == "List-Unsubscribe=One-Click"

    def _client(self):
        from flask import Flask
        from sincor2.blueprints.unsubscribe import unsubscribe_bp
        app = Flask(__name__)
        app.register_blueprint(unsubscribe_bp)
        return app.test_client()

    def test_get_unsubscribe_suppresses_and_send_path_honors_it(self, armed_env):
        from sincor2 import comms_compliance
        token = comms_compliance.make_unsubscribe_token("ops@example.com")
        assert token
        resp = self._client().get(f"/unsubscribe/{token}")
        assert resp.status_code == 200
        assert comms_compliance.is_suppressed("ops@example.com")
        from sincor2.comms_gating import outreach_send_allowed
        assert outreach_send_allowed("ops@example.com") == (False, "recipient_suppressed")
        from sincor2.outreach_engine import OutreachEngine
        fake = _FakeResend()
        assert OutreachEngine().send_outreach_email(
            {"email": "ops@example.com", "name": "Ops"}, fake
        ) is False
        assert fake.emails.sent == []

    def test_post_one_click_unsubscribe(self, armed_env):
        from sincor2 import comms_compliance
        token = comms_compliance.make_unsubscribe_token("ops@example.com")
        resp = self._client().post(
            f"/unsubscribe/{token}", data={"List-Unsubscribe": "One-Click"}
        )
        assert resp.status_code == 200
        assert comms_compliance.is_suppressed("ops@example.com")

    def test_tampered_token_fails_closed(self, armed_env):
        from sincor2 import comms_compliance
        token = comms_compliance.make_unsubscribe_token("ops@example.com")
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        client = self._client()
        assert client.get(f"/unsubscribe/{tampered}").status_code == 404
        assert client.post(f"/unsubscribe/{tampered}").status_code == 404
        assert comms_compliance.verify_unsubscribe_token(tampered) is None
        assert not comms_compliance.is_suppressed("ops@example.com")

    def test_garbage_token_fails_closed(self, armed_env):
        client = self._client()
        assert client.get("/unsubscribe/not-a-token").status_code == 404


# ===========================================================================
# 5. Caps (per-domain + global, read live) and the kill switch
# ===========================================================================

class TestCapsAndKillSwitch:
    def test_per_domain_cap_blocks_fourth_send_same_day(self, armed_env):
        from sincor2 import comms_compliance
        for local in ("a", "b", "c"):
            comms_compliance.record_send(f"{local}@foo.example")
        from sincor2.outreach_engine import OutreachEngine
        engine = OutreachEngine()
        fake = _FakeResend()
        assert engine.send_outreach_email(
            {"email": "d@foo.example", "name": "D"}, fake
        ) is False
        assert fake.emails.sent == []
        # A different domain is unaffected by foo.example's cap.
        assert engine.send_outreach_email(
            {"email": "other@bar.example", "name": "O"}, fake
        ) is True
        assert len(fake.emails.sent) == 1

    def test_global_daily_cap_is_read_live(self, armed_env):
        from sincor2 import comms_compliance
        from sincor2.outreach_engine import OutreachEngine
        engine = OutreachEngine()  # constructed while the default cap (40) applies
        armed_env.setenv("OUTREACH_DAILY_LIMIT", "2")  # tightened AFTER construction
        comms_compliance.record_send("a@foo.example")
        comms_compliance.record_send("other@bar.example")
        fake = _FakeResend()
        assert engine.send_outreach_email(
            {"email": "ops@example.com", "name": "Ops"}, fake
        ) is False
        assert fake.emails.sent == []

    def test_kill_switch_engaged_mid_flow_stops_sends(self, armed_env):
        from sincor2.comms_gating import outreach_send_allowed
        assert outreach_send_allowed("ops@example.com") == (True, "ok")
        armed_env.setenv("SINCOR_COMMS_KILL_SWITCH", "1")
        from sincor2.outreach_engine import OutreachEngine
        fake = _FakeResend()
        assert OutreachEngine().send_outreach_email(
            {"email": "ops@example.com", "name": "Ops"}, fake
        ) is False
        assert fake.emails.sent == []


# ===========================================================================
# 6. Copy: USDC pack rail at /pricing
# ===========================================================================

class TestCopy:
    def test_buy_url_is_pricing(self):
        from sincor2.outreach_engine import BUY_URL
        assert BUY_URL == "https://getsincor.com/pricing"

    def test_email_copy_describes_usdc_pack_rail(self, armed_env):
        from sincor2.outreach_engine import OutreachEngine
        engine = OutreachEngine()
        html = engine._build_email_html({"name": "Ops", "id": "lead1"})
        assert "SINC monthly or AXM one-time" not in html
        assert "USDC" in html
        assert "getsincor.com/pricing" in html


# ===========================================================================
# 7. SPF/DKIM/DMARC boot check — logged, never fatal
# ===========================================================================

class _FakeTXT:
    def __init__(self, text):
        self._text = text

    def to_text(self):
        return f'"{self._text}"'


class TestDnsAuthCheck:
    def test_reports_spf_and_dmarc_presence(self, monkeypatch):
        import dns.exception
        import dns.resolver

        from sincor2 import comms_compliance

        def fake_resolve(name, rdtype, **kwargs):
            if name == "getsincor.com":
                return [_FakeTXT("v=spf1 include:_spf.example.net -all")]
            if name == "_dmarc.getsincor.com":
                return [_FakeTXT("v=DMARC1; p=none")]
            raise dns.resolver.NoAnswer()

        monkeypatch.setattr(dns.resolver, "resolve", fake_resolve)
        result = comms_compliance.check_sender_dns_auth("getsincor.com")
        assert result["spf"] is True
        assert result["dmarc"] is True

    def test_dns_failure_is_not_fatal(self, monkeypatch):
        import dns.resolver

        from sincor2 import comms_compliance

        def boom(name, rdtype, **kwargs):
            raise OSError("dns down")

        monkeypatch.setattr(dns.resolver, "resolve", boom)
        result = comms_compliance.check_sender_dns_auth("getsincor.com")
        assert result["spf"] is not True
        assert result["dmarc"] is not True
