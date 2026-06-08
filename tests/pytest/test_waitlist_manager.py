"""Unit tests for WaitlistManager business logic."""
from __future__ import annotations

import pytest

from sincor2.waitlist_system import WaitlistManager


@pytest.fixture()
def manager(tmp_path):
    """Return a fresh WaitlistManager backed by a temp SQLite DB."""
    return WaitlistManager(db_path=str(tmp_path / "wl.db"))


# ---------------------------------------------------------------------------
# Email validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("email", [
    "user@example.com",
    "first.last+tag@sub.domain.org",
    "x@y.io",
])
def test_validate_email_accepts_valid(manager, email):
    assert manager.validate_email(email) is True


@pytest.mark.parametrize("email", [
    "notanemail",
    "missing@",
    "@nodomain.com",
    "no space@example.com",
    "",
])
def test_validate_email_rejects_invalid(manager, email):
    assert manager.validate_email(email) is False


# ---------------------------------------------------------------------------
# Hash determinism
# ---------------------------------------------------------------------------

def test_hash_email_is_deterministic(manager):
    h1 = manager.hash_email("User@Example.COM")
    h2 = manager.hash_email("user@example.com")
    assert h1 == h2


def test_hash_email_different_emails_differ(manager):
    assert manager.hash_email("a@example.com") != manager.hash_email("b@example.com")


# ---------------------------------------------------------------------------
# Priority scoring
# ---------------------------------------------------------------------------

def test_priority_score_enterprise_team_and_high_revenue(manager):
    score = manager.calculate_priority_score({
        "team_size": "50+ employees",
        "monthly_revenue": "$100k+ MRR",
        "industry": "SaaS",
        "pain_points": "",
    })
    # 50 (team) + 40 (revenue) + 20 (industry) = 110
    assert score >= 100


def test_priority_score_solo_freelancer_low_revenue(manager):
    score = manager.calculate_priority_score({
        "team_size": "1",
        "monthly_revenue": "under $1k",
        "industry": "art",
        "pain_points": "",
    })
    # minimum tier for each category → should be below enterprise score
    assert score < 50


def test_priority_score_urgent_pain_points_adds_bonus(manager):
    base = manager.calculate_priority_score({"team_size": "", "monthly_revenue": "", "industry": "", "pain_points": ""})
    urgent = manager.calculate_priority_score({"team_size": "", "monthly_revenue": "", "industry": "", "pain_points": "urgent issue"})
    assert urgent > base


# ---------------------------------------------------------------------------
# add_to_waitlist — happy path
# ---------------------------------------------------------------------------

def test_add_to_waitlist_success(manager, monkeypatch):
    # Patch flask request context dependency
    monkeypatch.setattr("sincor2.waitlist_system.request", None, raising=False)
    result = manager.add_to_waitlist({"email": "new@example.com", "name": "Alice"})
    assert result["success"] is True
    assert "position" in result
    assert "priority_score" in result


def test_add_to_waitlist_defaults_product_interest(manager, monkeypatch):
    monkeypatch.setattr("sincor2.waitlist_system.request", None, raising=False)
    result = manager.add_to_waitlist({"email": "nointerest@example.com"})
    assert result["success"] is True


def test_add_to_waitlist_duplicate_rejected(manager, monkeypatch):
    monkeypatch.setattr("sincor2.waitlist_system.request", None, raising=False)
    manager.add_to_waitlist({"email": "dup@example.com"})
    result = manager.add_to_waitlist({"email": "dup@example.com"})
    assert result["success"] is False
    assert "already registered" in result["error"]


def test_add_to_waitlist_case_insensitive_dedup(manager, monkeypatch):
    monkeypatch.setattr("sincor2.waitlist_system.request", None, raising=False)
    manager.add_to_waitlist({"email": "Case@Example.com"})
    result = manager.add_to_waitlist({"email": "case@example.com"})
    assert result["success"] is False


def test_add_to_waitlist_invalid_email(manager, monkeypatch):
    monkeypatch.setattr("sincor2.waitlist_system.request", None, raising=False)
    result = manager.add_to_waitlist({"email": "bad-email"})
    assert result["success"] is False
    assert "Invalid email" in result["error"]


# ---------------------------------------------------------------------------
# get_analytics
# ---------------------------------------------------------------------------

def test_get_analytics_returns_expected_keys(manager, monkeypatch):
    monkeypatch.setattr("sincor2.waitlist_system.request", None, raising=False)
    manager.add_to_waitlist({"email": "analytics@example.com", "product_interest": "Growth Engine"})
    analytics = manager.get_analytics()
    assert "products" in analytics
    assert "total_signups" in analytics
    assert analytics["total_signups"] >= 1


def test_get_analytics_counts_product_signups(manager, monkeypatch):
    monkeypatch.setattr("sincor2.waitlist_system.request", None, raising=False)
    for i in range(3):
        manager.add_to_waitlist({"email": f"u{i}@example.com", "product_interest": "Ops Core"})
    analytics = manager.get_analytics()
    assert analytics["products"].get("Ops Core", 0) >= 3
