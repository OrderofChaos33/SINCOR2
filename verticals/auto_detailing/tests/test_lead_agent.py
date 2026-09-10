"""ingest → score rubric."""

from __future__ import annotations

from verticals.auto_detailing.lead_agent import DetailingLeadAgent, score_lead
from verticals.auto_detailing.schemas import LeadIngestRequest, Vehicle


def test_ingest_then_score_hot_ceramic():
    agent = DetailingLeadAgent()
    result = agent.ingest(
        {
            "source": "google",
            "name": "Ava Chen",
            "email": "ava@example.com",
            "message": "Need ceramic coating and paint correction on my Porsche",
            "vehicle": {"year": 2023, "make": "Porsche", "model": "911", "condition": "daily"},
            "photo_urls": ["https://cdn.example/hood.jpg"],
        }
    )
    ranking = result["ranking"]
    assert ranking["score"] >= 78
    assert ranking["band"] == "hot"
    assert ranking["next_action"] == "book_now"
    assert "ceramic" in ranking["matched_intents"]
    assert ranking["rubric"]["photo_lift"] == 0.12
    assert ranking["rubric"]["identity_lift"] == 0.06


def test_anonymous_tiktok_wash_is_nurture():
    ranking = score_lead(
        LeadIngestRequest(
            source="tiktok",
            message="how much for a wash",
            vehicle=Vehicle(),
        )
    )
    assert ranking["band"] == "nurture"
    assert ranking["next_action"] == "sequence"
    assert ranking["score"] < 58
