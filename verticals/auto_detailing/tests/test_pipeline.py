"""End-to-end pipeline on demo data — zero external calls."""

from __future__ import annotations

from verticals.auto_detailing.config import live_send_enabled
from verticals.auto_detailing.pipeline import run_pipeline
from verticals.auto_detailing.send_gate import PENDING


def test_pipeline_hot_lead_quotes_and_queues(store, monkeypatch):
    monkeypatch.delenv("CHROMA_LIVE_SEND", raising=False)
    assert live_send_enabled() is False
    result = run_pipeline(
        {
            "source": "google",
            "name": "Ava Chen",
            "email": "ava@example.com",
            "phone": "555-0100",
            "message": "Need ceramic coating on my BMW X5",
            "vehicle": {"year": 2022, "make": "BMW", "body_style": "suv"},
            "photo_urls": ["https://cdn.example/x5.jpg"],
        },
        store=store,
    )
    assert result["lead"]["ranking"]["band"] == "hot"
    assert result["quote"]["package_id"] == "ceramic"
    assert result["quote"]["deposit"] > 0
    assert result["booking"]["calendly_url"].startswith("https://calendly.com/")
    assert result["outreach"]["queued_ids"]
    pending = store.list_outbound(PENDING)
    assert pending
    assert all(item["status"] == PENDING for item in pending)
    assert store.get_lead(result["lead"]["lead_id"])["status"] in {
        "quoted",
        "ready_to_book",
        "weather_hold",
    }


def test_pipeline_nurture_does_not_book(store):
    result = run_pipeline(
        {
            "source": "tiktok",
            "name": "Sam",
            "message": "cool cars",
        },
        store=store,
    )
    assert result["lead"]["ranking"]["band"] == "nurture"
    assert result["quote"] is None
    assert result["booking"] is None
    assert result["outreach"]["queued_ids"]
