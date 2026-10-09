"""WP2 comms tests: purpose classification, suppression, adapters, publishing.

Owner decisions enforced:
- D1: outreach default OFF, suppression list checked on every send
- D2: transactional email only; marketing always blocked_policy

Every test uses the real CommsAdapter / SuppressionList / publishers
with trap-free shadow boundaries. Nothing here sends, publishes, or
touches a provider.
"""

import os

import pytest

from sincor2.comms.adapters import CommsAdapter, CommsIntent
from sincor2.comms.classify import (
    classify_email_purpose,
    is_marketing_allowed,
)
from sincor2.comms.publishing import (
    get_farcaster_publisher,
    get_wordpress_publisher,
)
from sincor2.comms.suppression import SuppressionList, normalize_email
from sincor2.outreach_scheduler import (
    is_outreach_explicitly_enabled,
    start_outreach_scheduler,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def suppression(tmp_path):
    return SuppressionList(file_path=str(tmp_path / "suppression.jsonl"))


@pytest.fixture
def adapter(suppression):
    return CommsAdapter(suppression=suppression)


def _tx_kwargs(**over):
    base = dict(
        recipient="customer@example.com",
        purpose="transactional",
        originator="background_worker",
        idempotency_key="test-1",
        template="welcome",
        subject="Welcome to SINCOR",
        has_service_relationship=True,
    )
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# (a) marketing email → blocked
# ---------------------------------------------------------------------------


def test_marketing_email_blocked(adapter):
    receipt = adapter.send_email(
        **_tx_kwargs(purpose="marketing", idempotency_key="mkt-1")
    )
    assert receipt.status == "blocked_policy"
    assert receipt.executed is False
    assert "d2_marketing_email_blocked" in receipt.policy_reason_codes


def test_marketing_shaped_email_cannot_claim_transactional():
    # Laundering attempt: label a cold-outreach email "transactional".
    with pytest.raises(ValueError, match="classification says marketing"):
        CommsIntent(
            effect_type="email.send",
            recipient="lead@example.com",
            purpose="transactional",
            originator="agent",
            idempotency_key="launder-1",
            template="outreach",
            subject="Quick question",
            has_service_relationship=False,
        )


def test_is_marketing_allowed_always_false():
    assert is_marketing_allowed() is False


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_classify_transactional():
    assert (
        classify_email_purpose(
            template="welcome",
            subject="Welcome!",
            has_service_relationship=True,
        )
        == "transactional"
    )


def test_classify_marketing_signal_wins():
    assert (
        classify_email_purpose(
            template="welcome_outreach",
            subject="Welcome!",
            has_service_relationship=True,
        )
        == "marketing"
    )


def test_classify_no_relationship_is_marketing():
    # Service relationship required; template alone insufficient.
    assert (
        classify_email_purpose(
            template="welcome",
            subject="Welcome!",
            has_service_relationship=False,
        )
        == "marketing"
    )


def test_classify_unknown_is_marketing():
    assert classify_email_purpose() == "marketing"


# ---------------------------------------------------------------------------
# (b) transactional email → proposal (not sent)
# ---------------------------------------------------------------------------


def test_transactional_email_proposes(adapter):
    receipt = adapter.send_email(**_tx_kwargs(idempotency_key="tx-1"))
    assert receipt.status == "would_execute"
    assert receipt.executed is False
    # "sent"/"delivered" are unrepresentable.
    assert receipt.status not in ("sent", "delivered", "settled", "paid")


def test_transactional_delivery_report_not_sent(adapter):
    adapter.send_email(**_tx_kwargs(idempotency_key="tx-2"))
    report = adapter.delivery_report()
    assert report["status"] == "not sent"


# ---------------------------------------------------------------------------
# (c) suppressed recipient → blocked
# ---------------------------------------------------------------------------


def test_suppressed_recipient_blocked(adapter, suppression):
    suppression.add("optout@example.com", reason="opt_out")
    receipt = adapter.send_email(
        **_tx_kwargs(
            recipient="optout@example.com",
            idempotency_key="tx-sup-1",
        )
    )
    assert receipt.status == "blocked_policy"
    assert "recipient_suppressed" in receipt.policy_reason_codes
    assert receipt.executed is False


def test_suppression_case_insensitive(suppression):
    suppression.add("User@Example.COM")
    assert suppression.is_suppressed("user@example.com") is True


def test_malformed_email_treated_as_suppressed(suppression):
    # Fail closed: malformed → suppressed → blocked.
    assert suppression.is_suppressed("not-an-email") is True
    assert suppression.is_suppressed("") is True
    assert suppression.is_suppressed(None) is True  # type: ignore[arg-type]


def test_suppression_persists_to_file(tmp_path):
    path = str(tmp_path / "supp.jsonl")
    s1 = SuppressionList(file_path=path)
    s1.add("gone@example.com", reason="opt_out")
    s2 = SuppressionList(file_path=path)
    assert s2.is_suppressed("gone@example.com") is True
    assert s2.count() == 1


def test_normalize_email_rejects_injection():
    assert normalize_email("a@b.com\nBcc: evil@x.com") is None
    assert normalize_email("a@b.com, c@d.com") is None


# ---------------------------------------------------------------------------
# Originator enforcement (contract)
# ---------------------------------------------------------------------------


def test_invalid_originator_rejected():
    with pytest.raises(ValueError, match="may not originate"):
        CommsIntent(
            effect_type="email.send",
            recipient="x@example.com",
            purpose="transactional",
            originator="customer",  # not in allowlist for email.send
            idempotency_key="orig-1",
            template="welcome",
            has_service_relationship=True,
        )


# ---------------------------------------------------------------------------
# (d) scheduler does not start when not explicitly enabled
# ---------------------------------------------------------------------------


def test_scheduler_off_when_unset(monkeypatch):
    monkeypatch.delenv("OUTREACH_ENABLED", raising=False)
    monkeypatch.delenv("AUTONOMOUS_AGENTS", raising=False)
    assert is_outreach_explicitly_enabled() is False
    assert start_outreach_scheduler() is None


def test_scheduler_off_when_false(monkeypatch):
    monkeypatch.setenv("OUTREACH_ENABLED", "false")
    assert is_outreach_explicitly_enabled() is False
    assert start_outreach_scheduler() is None


def test_scheduler_off_when_autonomous_true_but_not_explicit(monkeypatch):
    # The old default-true chain: AUTONOMOUS_AGENTS=true used to imply
    # outreach on. That chain is broken; explicit flag required.
    monkeypatch.setenv("AUTONOMOUS_AGENTS", "true")
    monkeypatch.delenv("OUTREACH_ENABLED", raising=False)
    assert is_outreach_explicitly_enabled() is False
    assert start_outreach_scheduler() is None


def test_scheduler_on_when_explicit(monkeypatch):
    monkeypatch.setenv("OUTREACH_ENABLED", "true")
    assert is_outreach_explicitly_enabled() is True
    # Do NOT actually start the scheduler in tests (would arm APScheduler).
    # The gate check above is the fail-closed proof.


def test_outreach_engine_defaults_disabled(monkeypatch):
    monkeypatch.delenv("OUTREACH_ENABLED", raising=False)
    monkeypatch.delenv("AUTONOMOUS_AGENTS", raising=False)
    from sincor2.outreach_engine import OutreachEngine

    engine = OutreachEngine.__new__(OutreachEngine)
    # Call the module function directly to avoid __init__ side effects.
    from sincor2.outreach_engine import _autonomous_on

    assert _autonomous_on() is False


# ---------------------------------------------------------------------------
# (e) WordPress / Farcaster publish → blocked_policy
# ---------------------------------------------------------------------------


def test_wordpress_publish_blocked():
    pub = get_wordpress_publisher()
    receipt = pub.publish(
        {"title": "Test", "body": "hello"},
        originator="agent",
        idempotency_key="wp-1",
    )
    assert receipt.status == "blocked_policy"
    assert receipt.executed is False


def test_farcaster_publish_blocked():
    pub = get_farcaster_publisher()
    receipt = pub.publish(
        {"text": "hello farcaster"},
        originator="agent",
        idempotency_key="fc-1",
    )
    assert receipt.status == "blocked_policy"
    assert receipt.executed is False


def test_publish_requires_idempotency_key():
    pub = get_wordpress_publisher()
    with pytest.raises(ValueError, match="idempotency_key"):
        pub.publish({"title": "x"}, idempotency_key="")


def test_publish_delivery_report_not_sent():
    pub = get_wordpress_publisher()
    assert pub.delivery_report()["status"] == "not sent"
