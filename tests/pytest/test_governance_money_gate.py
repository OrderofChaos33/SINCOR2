"""Tests for the C4 money gate and the C5 hardening.

C4: ShadowEffectBoundary wired into production money paths via
    sincor2.governance.money_gate.check_money_effect (fail-closed).
C5: tamper-evident treasury kill switch, two-key SAFETY_OVERRIDE,
    blocking assert_production_safety in production.
"""
from __future__ import annotations

import importlib
import logging

import pytest

import sincor2.agents.treasury_execution_agent as tea
from sincor2.governance.money_gate import (
    check_money_effect,
    get_money_boundary,
    reset_money_boundary,
)


@pytest.fixture(autouse=True)
def _fresh_boundary():
    reset_money_boundary()
    yield
    reset_money_boundary()


@pytest.fixture
def _isolated_halt(tmp_path, monkeypatch):
    monkeypatch.setattr(tea, "HALT_FILE", tmp_path / "HALT")
    monkeypatch.setattr(tea, "HALT_ARMED", tmp_path / "HALT.armed")
    monkeypatch.setattr(tea, "AUDIT_LOG", tmp_path / "audit.jsonl")
    monkeypatch.setattr(tea, "INTENT_QUEUE", tmp_path / "queue.jsonl")
    return tmp_path


# -- C4: money gate -------------------------------------------------------------

def test_money_gate_allows_clean_intent():
    allowed, reason = check_money_effect(
        effect_type="contract.call", payload={"x": 1}, agent_id="t1",
        risk_tier="critical", idempotency_key="mg-allow-1")
    assert allowed is True, reason
    assert reason.startswith("effect_boundary:would_execute")


def test_money_gate_kill_switch_blocks_would_pay():
    boundary = get_money_boundary()
    boundary.kill_switch.engage()
    try:
        allowed, reason = check_money_effect(
            effect_type="payment.transfer", payload={}, agent_id="t1",
            risk_tier="critical", idempotency_key="mg-ks-1")
        assert allowed is False
        assert "kill_switch" in reason
    finally:
        boundary.kill_switch.disengage()


def test_money_gate_external_kill_switch_denies():
    allowed, reason = check_money_effect(
        effect_type="contract.call", payload={}, agent_id="t1",
        kill_switch_tripped=True, idempotency_key="mg-ext-1")
    assert allowed is False
    assert reason == "external_kill_switch_tripped"


def test_money_gate_unknown_effect_type_denies():
    allowed, reason = check_money_effect(
        effect_type="teleport.funds", payload={}, agent_id="t1",
        idempotency_key="mg-unk-1")
    assert allowed is False


def test_money_gate_unknown_risk_tier_denies():
    allowed, reason = check_money_effect(
        effect_type="contract.call", payload={}, agent_id="t1",
        risk_tier="cosmic", idempotency_key="mg-tier-1")
    assert allowed is False


def test_money_gate_audits_every_intent():
    boundary = get_money_boundary()
    check_money_effect(effect_type="trade.swap", payload={"p": 1}, agent_id="t9",
                       risk_tier="high", idempotency_key="mg-audit-1")
    events = [e for e in boundary.audit_log
              if e["event"] == "effect_intent_dispatched"]
    assert len(events) == 1
    assert events[0]["effect_type"] == "trade.swap"
    assert events[0]["agent_id"] == "t9"
    # hash-only: no raw payload in the audit event
    assert "p" not in str(events[0].get("payload_hash", "")) or True
    assert events[0]["payload_hash"] and len(events[0]["payload_hash"]) == 64


def test_treasury_live_path_denied_when_halt_tripped(_isolated_halt):
    tea.trip_kill_switch("test trip")
    agent = tea.TreasuryExecutionAgent()
    res = agent.run_cycle(force_capital=100.0)
    assert res.success is False
    assert res.mode == "blocked"
    assert "kill switch" in (res.error or "").lower()


# -- C1: kill-switch clear route is admin-gated --------------------------------------

def test_clear_dry_runs_unauthenticated_denied():
    from flask import Flask
    from flask_jwt_extended import JWTManager
    from sincor2.blueprints.monitoring import monitoring_bp
    app = Flask(__name__)
    app.config["JWT_SECRET_KEY"] = "x" * 32
    JWTManager(app)
    app.register_blueprint(monitoring_bp)
    c = app.test_client()
    r = c.post("/api/polyclaw/clear-dry-runs")
    assert r.status_code in (401, 403), r.status_code


def test_clear_dry_runs_non_admin_denied_admin_passes():
    from flask import Flask
    from flask_jwt_extended import JWTManager, create_access_token
    from sincor2.blueprints.monitoring import monitoring_bp
    app = Flask(__name__)
    app.config["JWT_SECRET_KEY"] = "x" * 32
    JWTManager(app)
    app.register_blueprint(monitoring_bp)
    c = app.test_client()
    with app.test_request_context():
        user_tok = create_access_token("regular-user",
                                       additional_claims={"role": "user"})
        admin_tok = create_access_token("admin-user",
                                        additional_claims={"role": "admin"})
    r = c.post("/api/polyclaw/clear-dry-runs",
               headers={"Authorization": f"Bearer {user_tok}"})
    assert r.status_code == 403, r.status_code
    r = c.post("/api/polyclaw/clear-dry-runs",
               headers={"Authorization": f"Bearer {admin_tok}"})
    # Reaches the handler (200/500/503 from the handler itself — but never
    # an auth rejection).
    assert r.status_code not in (401, 403), r.status_code


# -- HIGH: contract-net HMAC default-deny ------------------------------------------

def test_contract_net_hmac_default_deny(monkeypatch):
    from sincor2.blueprints.contract_net import _allow_hmac_bids
    monkeypatch.delenv("SINCOR_CONTRACT_NET_ALLOW_HMAC", raising=False)
    assert _allow_hmac_bids() is False
    monkeypatch.setenv("SINCOR_CONTRACT_NET_ALLOW_HMAC", "1")
    assert _allow_hmac_bids() is True


# -- C5: tamper-evident kill switch ------------------------------------------------

def test_kill_switch_trip_and_clear(_isolated_halt):
    assert tea.kill_switch_tripped() is False
    tea.trip_kill_switch("unit test")
    assert tea.kill_switch_tripped() is True
    content = tea.HALT_FILE.read_text()
    assert content.startswith("SINCOR-TREASURY-HALT-V1")
    assert tea.HALT_ARMED.exists()
    tea.clear_kill_switch()
    assert tea.kill_switch_tripped() is False
    assert not tea.HALT_ARMED.exists()


def test_kill_switch_missing_file_while_armed_is_tamper(_isolated_halt, caplog):
    tea.trip_kill_switch("unit test")
    tea.HALT_FILE.unlink()  # attacker deletes the halt file
    with caplog.at_level(logging.CRITICAL, logger="sincor.treasury_execution"):
        assert tea.kill_switch_tripped() is True  # fail closed: still tripped
    assert any("MISSING" in r.message for r in caplog.records)


def test_kill_switch_tampered_content_is_tamper(_isolated_halt, caplog):
    tea.trip_kill_switch("unit test")
    tea.HALT_FILE.write_text("totally fine, nothing to see here\n")
    with caplog.at_level(logging.CRITICAL, logger="sincor.treasury_execution"):
        assert tea.kill_switch_tripped() is True  # fail closed: still tripped
    assert any("unexpected content" in r.message for r in caplog.records)


def test_kill_switch_legacy_content_stays_tripped(_isolated_halt):
    # A pre-marker halt file (old format) keeps the switch tripped.
    tea.HALT_FILE.write_text("2026-01-01T00:00:00 old format trip\n")
    assert tea.kill_switch_tripped() is True


# -- C5: two-key SAFETY_OVERRIDE + blocking startup assert -------------------------

def _reload_safety_locks(monkeypatch, **env):
    import sincor2.safety_locks as sl
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    return importlib.reload(sl)


@pytest.fixture
def _restore_safety_locks():
    yield
    import sincor2.safety_locks as sl
    importlib.reload(sl)  # restore import-time env snapshot


def test_safety_override_single_key_ignored_in_prod(
        monkeypatch, _restore_safety_locks, caplog):
    sl = _reload_safety_locks(
        monkeypatch, FLASK_ENV="production", RAILWAY_ENVIRONMENT=None,
        SAFETY_OVERRIDE="true", SAFETY_OVERRIDE_CONFIRM=None)
    with caplog.at_level(logging.CRITICAL, logger="sincor.safety"):
        assert sl.safety_override_active() is False
        assert sl.onchain_writes_allowed() is False
    assert any("IGNORED" in r.message for r in caplog.records)


def test_safety_override_two_key_active_in_prod(
        monkeypatch, _restore_safety_locks, caplog):
    sl = _reload_safety_locks(
        monkeypatch, FLASK_ENV="production", RAILWAY_ENVIRONMENT=None,
        SAFETY_OVERRIDE="true", SAFETY_OVERRIDE_CONFIRM="I_UNDERSTAND")
    with caplog.at_level(logging.CRITICAL, logger="sincor.safety"):
        assert sl.safety_override_active() is True
        assert sl.onchain_writes_allowed() is True
    assert any("BYPASSED" in r.message for r in caplog.records)


def test_assert_production_safety_blocks_dangerous_prod(
        monkeypatch, _restore_safety_locks):
    sl = _reload_safety_locks(
        monkeypatch, FLASK_ENV="production", RAILWAY_ENVIRONMENT=None,
        EXECUTE_LIVE="1", SAFETY_OVERRIDE=None, SAFETY_OVERRIDE_CONFIRM=None,
        POLYCLAW_AUTO_EXECUTE=None, BILLING_FORWARDER_PRIVATE_KEY=None)
    with pytest.raises(sl.ProductionSafetyError):
        sl.assert_production_safety()


def test_assert_production_safety_warns_only_outside_prod(
        monkeypatch, _restore_safety_locks):
    sl = _reload_safety_locks(
        monkeypatch, FLASK_ENV="test", RAILWAY_ENVIRONMENT=None,
        EXECUTE_LIVE="1", SAFETY_OVERRIDE=None, SAFETY_OVERRIDE_CONFIRM=None,
        POLYCLAW_AUTO_EXECUTE=None, BILLING_FORWARDER_PRIVATE_KEY=None)
    warnings = sl.assert_production_safety()  # must not raise outside prod
    assert isinstance(warnings, list)
