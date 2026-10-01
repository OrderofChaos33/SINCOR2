"""P24 content-screener interface tests.

Covers the pluggable ContentScreener contract: fail-closed default,
the deny-list standing screener, the deferred stub, a test-double allow
screener, onboarding wiring, and the no-full-text-logging guarantee.
Self-contained (no app imports).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sincor2.defi.p24 import onboarding, policy, screener
from src.sincor2.defi.p24.policy import PolicyViolation
from src.sincor2.defi.p24.screener import (
    ContentScreener,
    DeferredScreener,
    DenyListScreener,
    ScreenDecision,
    ScreenerDenied,
    configure_screener,
    get_screener,
    require_screened,
    reset_screener,
)


@pytest.fixture(autouse=True)
def _clean_screener_state(monkeypatch):
    reset_screener()
    monkeypatch.delenv("P24_SCREENER", raising=False)
    yield
    reset_screener()
    monkeypatch.delenv("P24_SCREENER", raising=False)


CLEAN = dict(name="Sunset Club", symbol="SUN",
             description="a community art project", bio="creator bio")
DIRTY = dict(name="Moon Fund", symbol="MOON",
             description="guaranteed 10x, buy now", bio="bio")


class AllowScreener:
    """Test double: permits everything, records what it saw."""

    screener_id = "test-allow"

    def __init__(self):
        self.calls = []

    def screen(self, name, symbol, description, bio):
        self.calls.append((name, symbol, description, bio))
        return ScreenDecision(True, "test double allows", self.screener_id)


# -- protocol shape -----------------------------------------------------------

def test_protocol_shape():
    for cls in (DenyListScreener, DeferredScreener, AllowScreener):
        inst = cls()
        assert isinstance(inst.screener_id, str)
        d = inst.screen(**CLEAN)
        assert isinstance(d, ScreenDecision)
        assert isinstance(d.allowed, bool)
        assert d.reason and d.screener_id and d.ruleset_version


# -- fail-closed default ------------------------------------------------------

def test_unconfigured_screener_is_deferred_and_denies():
    got = get_screener()
    assert isinstance(got, DeferredScreener)
    with pytest.raises(ScreenerDenied) as exc:
        require_screened(got, "alice", **CLEAN)
    assert exc.value.reason == "deferred: no screener configured"
    assert exc.value.screener_id == "deferred"


def test_env_deferred_selects_stub(monkeypatch):
    monkeypatch.setenv("P24_SCREENER", "deferred")
    assert isinstance(get_screener(), DeferredScreener)
    assert isinstance(screener.default_onboarding_screener(), DeferredScreener)


def test_env_deny_list_keeps_standing_screen(monkeypatch):
    monkeypatch.setenv("P24_SCREENER", "deny-list")
    # the process registry stays fail-closed; the onboarding standing
    # screen remains the deny-list
    assert isinstance(get_screener(), DeferredScreener)
    assert isinstance(screener.default_onboarding_screener(), DenyListScreener)


def test_configure_none_returns_to_fail_closed():
    configure_screener(DenyListScreener())
    assert isinstance(get_screener(), DenyListScreener)
    configure_screener(None)
    assert isinstance(get_screener(), DeferredScreener)


def test_configured_screener_wins_for_onboarding():
    configure_screener(DeferredScreener())
    agent = onboarding.OnboardingAgent()
    with pytest.raises(ScreenerDenied) as exc:
        agent.register("alice", now=0.0, **CLEAN)
    assert exc.value.screener_id == "deferred"


# -- deny-list standing screener ----------------------------------------------

def test_deny_list_allows_clean():
    version = require_screened(DenyListScreener(), "alice", **CLEAN)
    assert version == "1.0.0"


def test_deny_list_denies_with_phrase_and_field():
    with pytest.raises(ScreenerDenied) as exc:
        require_screened(DenyListScreener(), "mallory", **DIRTY)
    denied = exc.value
    assert denied.field == "description"
    assert denied.matched_phrase == "guaranteed 10x"
    assert denied.screener_id == "deny-list"
    # backward compat: existing callers catch PolicyViolation
    assert isinstance(denied, PolicyViolation)


# -- stub never fabricates an allow -------------------------------------------

def test_deferred_stub_never_allows():
    stub = DeferredScreener()
    for kwargs in (CLEAN, DIRTY,
                   dict(name="x", symbol="X", description="", bio="")):
        d = stub.screen(**kwargs)
        assert d.allowed is False
        assert d.reason == "deferred: no screener configured"


# -- test-double allow screener permits ---------------------------------------

def test_allow_screener_permits_and_is_called():
    dbl = AllowScreener()
    version = require_screened(dbl, "alice", **CLEAN)
    assert version == "1.0.0"
    assert len(dbl.calls) == 1


# -- onboarding wiring ---------------------------------------------------------

def test_onboarding_default_screens_like_before():
    agent = onboarding.OnboardingAgent()
    reg = agent.register("alice", now=0.0, **CLEAN)
    assert reg.policy_version == "1.0.0"
    with pytest.raises(PolicyViolation):
        agent.register("mallory", now=0.0, **DIRTY)
    assert len(agent.rejections) == 1
    assert agent.rejections[0].matched_phrase == "guaranteed 10x"


def test_onboarding_uses_injected_screener():
    dbl = AllowScreener()
    agent = onboarding.OnboardingAgent(screener=dbl)
    reg = agent.register("alice", now=0.0, **CLEAN)
    assert reg.token.symbol == "SUN"
    assert len(dbl.calls) == 1


def test_onboarding_deferred_screener_halts_issuance():
    agent = onboarding.OnboardingAgent(screener=DeferredScreener())
    with pytest.raises(ScreenerDenied) as exc:
        agent.register("alice", now=0.0, **CLEAN)
    assert exc.value.screener_id == "deferred"
    assert agent.registrations == {}


def test_onboarding_env_deferred_halts_issuance(monkeypatch):
    monkeypatch.setenv("P24_SCREENER", "deferred")
    agent = onboarding.OnboardingAgent()
    with pytest.raises(ScreenerDenied):
        agent.register("alice", now=0.0, **CLEAN)


def test_onboarding_rejection_log_never_stores_text():
    agent = onboarding.OnboardingAgent()
    secret = "guaranteed 10x, buy now"
    with pytest.raises(ScreenerDenied):
        agent.register("mallory", "Moon Fund", "MOON", secret, "bio", now=0.0)
    rej = agent.rejections[0]
    assert rej.matched_phrase == "guaranteed 10x"
    assert secret not in repr(rej)


# -- issuance route calls the screener ------------------------------------------

def test_issuance_path_enforcement_point_is_onboarding():
    """The route calls agent.register; enforcement lives in onboarding, so a
    deferred screener denies even clean metadata at the single choke point."""
    agent = onboarding.OnboardingAgent(screener=DeferredScreener())
    with pytest.raises(ScreenerDenied):
        agent.register("agent-1", now=0.0, **CLEAN)


# -- logging: decisions logged, text never --------------------------------------

def test_deny_logs_reason_not_text(caplog):
    agent = onboarding.OnboardingAgent()
    secret = "guaranteed 10x, buy now"
    with caplog.at_level(logging.WARNING, logger="sincor2.defi.p24.screener"):
        with pytest.raises(ScreenerDenied):
            agent.register("mallory", "Moon Fund", "MOON", secret, "bio",
                           now=0.0)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "guaranteed 10x" in logged  # the matched phrase is fine
    assert secret not in logged        # the full submitted text is not


def test_allow_logs_screener_and_ruleset(caplog):
    with caplog.at_level(logging.INFO, logger="sincor2.defi.p24.screener"):
        require_screened(DenyListScreener(), "alice", **CLEAN)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "deny-list" in logged and "1.0.0" in logged
