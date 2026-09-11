"""One test per remaining agent."""

from __future__ import annotations

from verticals.auto_detailing.copy_agent import DetailingCopyAgent
from verticals.auto_detailing.engagement_agent import DetailingEngagementAgent
from verticals.auto_detailing.presence_agent import DetailingPresenceAgent
from verticals.auto_detailing.social_agent import DetailingSocialAgent


def test_copy_google_rsa():
    out = DetailingCopyAgent().generate(
        {"channel": "google_rsa", "city": "Dubuque", "package_id": "ceramic"}
    )
    assert len(out["headlines"]) >= 8
    assert out["channel"] == "google_rsa"


def test_presence_seo_bounds():
    out = DetailingPresenceAgent().optimize(
        {
            "shop_name": "Northline Detail",
            "city": "Dubuque",
            "region": "IA",
            "reviews": [{"author": "Ken", "rating": 5, "text": "Ceramic looks wet"}],
            "gallery": [{"src": "/gallery/hero-amg.jpg", "vehicle": "AMG", "kind": "after"}],
        }
    )
    assert 50 <= len(out["title"]) <= 70
    assert out["json_ld"]["@type"]


def test_social_schedule_five_posts():
    out = DetailingSocialAgent().schedule({"cadence_per_week": 5})
    assert len(out["posts"]) == 5
    assert out["queued_ids"] == []


def test_engagement_ceramic_returns_calendly():
    out = DetailingEngagementAgent().engage(
        {"visitor_message": "I want ceramic on my SUV"}
    )
    assert out["package_id"] == "ceramic"
    assert "calendly_url" in out


def test_missed_call_does_not_claim_sent(store):
    out = DetailingEngagementAgent().missed_call(
        {"phone": "+15551212", "enqueue": True, "store": store}
    )
    assert out["sent"] is False
    assert out["queued"] is True
    item = store.get_outbound(out["queued_id"])
    assert item["status"] == "pending_approval"
