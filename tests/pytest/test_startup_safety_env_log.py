"""Startup safety-env echo (2026-10-12 outage fix).

On 2026-10-12 getsincor.com was down ~70 minutes because Railway had
POLYCLAW_AUTO_EXECUTE=true, tripping the ProductionSafetyError boot guard —
and nothing in the startup logs echoed the safety-critical env flags, so the
cause was invisible until manually diagnosed.

log_safety_env() echoes every safety-critical flag's effective value at INFO
on every boot (``SAFETY_ENV POLYCLAW_AUTO_EXECUTE=false``) and logs WARNING
for any flag in a dangerous state. Secrets are never logged: the forwarder
key and the override-confirm key are reported as set/unset only.
"""

from __future__ import annotations

import logging

import pytest

from sincor2 import safety_locks
from sincor2.safety_locks import log_safety_env

_ALL_FLAGS = [
    "RAILWAY_ENVIRONMENT",
    "FLASK_ENV",
    "POLYCLAW_AUTO_EXECUTE",
    "EXECUTE_LIVE",
    "COMPLIANCE_CONFIDENTIAL",
    "COMPLIANCE_LOG_TO_STDOUT",
    "ALLOW_ONCHAIN_WRITES",
    "SAFETY_OVERRIDE",
    "BILLING_FORWARDER_PRIVATE_KEY",
    "SAFETY_OVERRIDE_CONFIRM",
]


@pytest.fixture
def clean_safety_env(monkeypatch):
    """Deterministic env: no safety flags set, nothing leaking from the shell."""
    for name in _ALL_FLAGS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _messages(caplog):
    return [r.getMessage() for r in caplog.records]


def test_safe_values_logged_at_info(clean_safety_env, caplog):
    clean_safety_env.setenv("POLYCLAW_AUTO_EXECUTE", "false")
    clean_safety_env.setenv("EXECUTE_LIVE", "0")
    clean_safety_env.setenv("COMPLIANCE_CONFIDENTIAL", "true")
    clean_safety_env.setenv("COMPLIANCE_LOG_TO_STDOUT", "false")
    clean_safety_env.setenv("RAILWAY_ENVIRONMENT", "production")
    with caplog.at_level(logging.INFO, logger="sincor.safety"):
        log_safety_env()
    msgs = _messages(caplog)
    for expected in [
        "SAFETY_ENV POLYCLAW_AUTO_EXECUTE=false",
        "SAFETY_ENV EXECUTE_LIVE=0",
        "SAFETY_ENV COMPLIANCE_CONFIDENTIAL=true",
        "SAFETY_ENV COMPLIANCE_LOG_TO_STDOUT=false",
        "SAFETY_ENV RAILWAY_ENVIRONMENT=production",
        "SAFETY_ENV BILLING_FORWARDER_PRIVATE_KEY=unset",
        "SAFETY_ENV SAFETY_OVERRIDE_CONFIRM=unset",
    ]:
        assert any(expected in m for m in msgs), f"missing INFO line: {expected}"
    assert not any("DANGEROUS" in m for m in msgs), "no flag is dangerous here"


def test_unset_flags_show_unset(clean_safety_env, caplog):
    with caplog.at_level(logging.INFO, logger="sincor.safety"):
        log_safety_env()
    msgs = _messages(caplog)
    assert any("SAFETY_ENV POLYCLAW_AUTO_EXECUTE=<unset>" in m for m in msgs)


@pytest.mark.parametrize(
    "name,value",
    [
        ("POLYCLAW_AUTO_EXECUTE", "true"),
        ("POLYCLAW_AUTO_EXECUTE", "TRUE"),  # guard compares case-insensitively
        ("EXECUTE_LIVE", "1"),
        ("COMPLIANCE_CONFIDENTIAL", "false"),
        ("COMPLIANCE_LOG_TO_STDOUT", "true"),
        ("SAFETY_OVERRIDE", "true"),
    ],
)
def test_dangerous_flag_emits_warning(clean_safety_env, caplog, name, value):
    clean_safety_env.setenv(name, value)
    with caplog.at_level(logging.INFO, logger="sincor.safety"):
        log_safety_env()
    msgs = _messages(caplog)
    assert any(
        name in m and "DANGEROUS" in m for m in msgs
    ), f"expected DANGEROUS warning for {name}={value}"
    # The INFO echo of the effective value is still present.
    assert any(f"SAFETY_ENV {name}=" in m for m in msgs)


def test_forwarder_key_never_logged(clean_safety_env, caplog):
    secret = "0xdeadbeef-super-secret-key-value"
    clean_safety_env.setenv("BILLING_FORWARDER_PRIVATE_KEY", secret)
    with caplog.at_level(logging.INFO, logger="sincor.safety"):
        log_safety_env()
    msgs = _messages(caplog)
    assert any("SAFETY_ENV BILLING_FORWARDER_PRIVATE_KEY=set" in m for m in msgs)
    assert not any(secret in m for m in msgs), "secret value must never appear in logs"


def test_override_confirm_logged_as_presence_only(clean_safety_env, caplog):
    clean_safety_env.setenv("SAFETY_OVERRIDE_CONFIRM", "I_UNDERSTAND")
    with caplog.at_level(logging.INFO, logger="sincor.safety"):
        log_safety_env()
    assert any(
        "SAFETY_ENV SAFETY_OVERRIDE_CONFIRM=set" in m for m in _messages(caplog)
    )


def test_guard_raises_but_echo_logged_first(clean_safety_env, caplog, monkeypatch):
    """The 2026-10-12 scenario: dangerous flag in prod raises, but the
    SAFETY_ENV echo lines are in the logs BEFORE the raise — the exact
    visibility that was missing during the outage.

    NOTE: names are resolved through the module object at call time because
    neighboring test files importlib.reload(safety_locks), which rebinds the
    ProductionSafetyError class object — a statically imported class would
    not match the raised instance after a reload.
    """
    clean_safety_env.setenv("RAILWAY_ENVIRONMENT", "production")
    clean_safety_env.setenv("POLYCLAW_AUTO_EXECUTE", "true")
    monkeypatch.setattr(safety_locks, "_IS_PROD", True)
    monkeypatch.setattr(safety_locks, "_OVERRIDE", False)
    with caplog.at_level(logging.INFO, logger="sincor.safety"):
        with pytest.raises(safety_locks.ProductionSafetyError):
            safety_locks.assert_production_safety()
    msgs = _messages(caplog)
    assert any("SAFETY_ENV POLYCLAW_AUTO_EXECUTE=true" in m for m in msgs)
    assert any("DANGEROUS" in m and "POLYCLAW_AUTO_EXECUTE" in m for m in msgs)


def test_guard_safe_config_still_passes_and_echoes(clean_safety_env, caplog, monkeypatch):
    clean_safety_env.setenv("POLYCLAW_AUTO_EXECUTE", "false")
    monkeypatch.setattr(safety_locks, "_IS_PROD", False)
    monkeypatch.setattr(safety_locks, "_OVERRIDE", False)
    with caplog.at_level(logging.INFO, logger="sincor.safety"):
        warnings = safety_locks.assert_production_safety()
    assert warnings == []
    assert any("SAFETY_ENV POLYCLAW_AUTO_EXECUTE=false" in m for m in _messages(caplog))
