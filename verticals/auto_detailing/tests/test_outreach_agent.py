"""Sequence ordering."""

from __future__ import annotations

from verticals.auto_detailing.outreach_agent import DetailingOutreachAgent, build_sequence
from verticals.auto_detailing.schemas import OutreachRequest


def test_quote_followup_days_are_0_2_5():
    steps = build_sequence(
        OutreachRequest(kind="quote_followup", name="Ava", package_id="ceramic", channel="email")
    )
    assert [s["day"] for s in steps] == [0, 2, 5]
    assert steps[0]["channel"] == "email"
    assert steps[1]["channel"] == "sms"
    assert steps[2]["channel"] == "email"
    assert "ceramic" in steps[0]["subject"].lower() or "Ceramic" in steps[0]["subject"]


def test_sequence_via_agent_preserves_order():
    agent = DetailingOutreachAgent()
    out = agent.sequence({"kind": "quote_followup", "name": "Ken", "package_id": "interior"})
    assert [s["day"] for s in out["steps"]] == [0, 2, 5]
    assert out["stop_on_book"] is True
    assert out["queued_ids"] == []


def test_enqueue_writes_pending_items(store):
    agent = DetailingOutreachAgent()
    out = agent.sequence(
        {
            "kind": "quote_followup",
            "name": "Ken",
            "package_id": "interior",
            "email": "ken@test",
            "lead_id": "LD-TEST",
            "enqueue": True,
            "store": store,
        }
    )
    assert len(out["queued_ids"]) == 3
    pending = store.list_outbound("pending_approval")
    assert len(pending) == 3
    assert all(item["status"] == "pending_approval" for item in pending)
