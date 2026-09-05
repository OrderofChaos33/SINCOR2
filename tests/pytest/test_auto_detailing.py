"""Unit + dispatch tests for the CHROMA auto detailing vertical."""

from __future__ import annotations

import json

from verticals.auto_detailing.agent import AutoDetailingAgent
from verticals.auto_detailing.lead_agent import DetailingLeadAgent
from verticals.auto_detailing.protocols import PACKAGES, deposit_for
from verticals.loader import instantiate_vertical_agents, load_agent_cards, resolve_vertical_agent


def test_pack_card_loads():
    cards = load_agent_cards()
    names = [c.get("name", "") for c in cards]
    assert any("Detailing" in n or "CHROMA" in n for n in names)
    detailing = next(c for c in cards if "Detailing" in c.get("name", "") or "CHROMA" in c.get("name", ""))
    skill_ids = {s["id"] for s in detailing["skills"]}
    assert {
        "detailing-lead-ingest",
        "detailing-booking",
        "detailing-presence",
        "detailing-social",
        "detailing-copy",
        "detailing-engage",
    } <= skill_ids


def test_agent_registered():
    agents = instantiate_vertical_agents()
    assert "auto_detailing_agent" in agents
    resolved = resolve_vertical_agent("detailing-booking", agents)
    assert resolved is not None
    assert resolved.name == "auto_detailing_agent"


def test_luxury_ceramic_scores_hot():
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
    assert result["ranking"]["score"] >= 78
    assert result["ranking"]["band"] == "hot"
    assert result["ranking"]["next_action"] == "book_now"


def test_ceramic_requires_deposit():
    assert deposit_for("ceramic") == 0.5
    assert deposit_for("express_wash") == 0.0
    agent = AutoDetailingAgent()
    out = agent.run(
        {
            "task_type": "quote_estimate",
            "payload": {
                "package_id": "ceramic",
                "vehicle": {"make": "BMW", "body_style": "suv"},
            },
            "correlation_id": "q-1",
        }
    )
    assert out["status"] == "success"
    assert out["result"]["deposit"] > 0
    assert out["result"]["vehicle_size"] == "suv"
    assert out["result"]["total"] > PACKAGES["ceramic"]["price"]


def test_calendly_url_prefills():
    agent = AutoDetailingAgent()
    out = agent.run(
        {
            "task_type": "calendly_handoff",
            "payload": {
                "package_id": "interior",
                "name": "Marcus Hale",
                "email": "marcus@example.com",
                "vehicle": {"year": 2019, "make": "Toyota", "model": "Tundra", "body_style": "truck"},
                "calendly_handle": "northline-detail",
            },
        }
    )
    assert out["status"] == "success"
    url = out["result"]["calendly_url"]
    assert url.startswith("https://calendly.com/northline-detail/interior-revival")
    assert "Marcus" in url or "name=Marcus" in url
    assert out["result"]["quote"]["vehicle_size"] == "truck"


def test_weather_hold_on_exterior():
    agent = AutoDetailingAgent()
    out = agent.run(
        {
            "task_type": "calendly_handoff",
            "payload": {
                "package_id": "maintenance_wash",
                "weather_precip_pct": 80,
                "vehicle": {"body_style": "sedan"},
            },
        }
    )
    assert out["result"]["weather_hold"] is True
    interior = agent.run(
        {
            "task_type": "calendly_handoff",
            "payload": {
                "package_id": "interior",
                "weather_precip_pct": 80,
                "vehicle": {"body_style": "sedan"},
            },
        }
    )
    assert interior["result"]["weather_hold"] is False


def test_seo_metadata_bounds():
    agent = AutoDetailingAgent()
    out = agent.run(
        {
            "task_type": "seo_optimize",
            "payload": {
                "shop_name": "Northline Detail",
                "city": "Dubuque",
                "region": "IA",
                "reviews": [{"author": "Ken", "rating": 5, "text": "Ceramic looks wet"}],
                "gallery": [{"src": "/gallery/hero-amg.jpg", "vehicle": "AMG", "kind": "after"}],
            },
        }
    )
    result = out["result"]
    assert 50 <= len(result["title"]) <= 70
    assert 120 <= len(result["meta_description"]) <= 160
    assert result["json_ld"]["@type"]
    assert result["seo_score"] >= 70


def test_engagement_returns_booking_link():
    agent = AutoDetailingAgent()
    out = agent.run(
        {
            "task_type": "landing_engage",
            "payload": {"visitor_message": "I want ceramic on my SUV"},
        }
    )
    assert out["status"] == "success"
    assert "calendly_url" in out["result"]
    assert out["result"]["package_id"] == "ceramic"


def test_social_calendar_and_copy():
    agent = AutoDetailingAgent()
    social = agent.run({"task_type": "social_schedule", "payload": {"cadence_per_week": 5}})
    assert social["result"]["posts"]
    assert len(social["result"]["posts"]) == 5
    copy = agent.run(
        {
            "task_type": "ad_copy",
            "payload": {"channel": "google_rsa", "city": "Dubuque", "package_id": "ceramic"},
        }
    )
    assert len(copy["result"]["headlines"]) >= 8


def test_dispatch_skill_id():
    from sincor2.vertical_dispatch import dispatch_vertical_task

    agents = instantiate_vertical_agents()
    output, error = dispatch_vertical_task(
        "detailing-lead-ingest",
        json.dumps(
            {
                "task_type": "lead_ingest",
                "payload": {
                    "source": "website",
                    "message": "full detail for a daily driver",
                    "name": "Sam",
                },
            }
        ),
        {"vertical_agents": agents},
    )
    assert error is None
    assert "success" in output
