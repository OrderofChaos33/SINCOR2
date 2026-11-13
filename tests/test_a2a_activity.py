"""Honest activity metrics (stream C): seeded ground-truth tests.

Seeds the auction store with a known scenario (N auctions, known bids, known
settlements, known 5% fee-ledger entries), hits GET /v1/a2a/activity, and
asserts every returned figure matches the seeded ground truth exactly.
Also covers the empty state (all zeros, correct labels, disclaimer).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


# ---------------------------------------------------------------------------
# Ground-truth scenario (all values hand-computed below).
#
# In-window tasks (created ~30 min ago):
#   t1 settled  winning_bid 10.0  agent-a wins, posted by poster-x
#   t2 settled  winning_bid 20.0  agent-b wins, posted by poster-x
#   t3 expired                        posted by poster-y
#   t4 open                           posted by poster-y   (not closed)
#   t6 failed                         posted by poster-z
# Out-of-window task: t5 settled (100 min ago), posted by poster-old.
#
# In-window bids: t1: agent-a 10.0, agent-b 9.0, agent-c 8.0
#                 t2: agent-b 20.0, agent-a 18.0
#                 t3: agent-d 5.0
# Out-of-window:  t5: agent-e 50.0
#
# Fee ledger (5% of settlements): 0.5 AXM (t1) + 1.0 AXM (t2), in window.
# Decoys (must be EXCLUDED): projected fee, non-AXM fee, non-fee inflow,
#                            out-of-window fee entry.
#
# Ground truth, 1h window:
#   auctions_opened = 5          -> auctions_per_hour = 5.0
#   bid_volume_axm  = 70.0       (6 bids)
#   settlement_rate = 2/4 = 0.5  (settled 2, closed 4)
#   fees            = 1.5 AXM    (2 events)
#   active_agents   = 7 (agent-a..d, poster-x, poster-y, poster-z)
#
# Ground truth, 2h window:
#   auctions_opened = 6, rate = 3/5 = 0.6, volume = 120.0,
#   fees = 4.0 (3 events), active = 9 (+agent-e, +poster-old)
# ---------------------------------------------------------------------------

MIN = 60_000


def _seed_store(now_ms: int):
    from sincor2.a2a_inbound import reset_fabric

    fabric = reset_fabric()
    t = now_ms - 30 * MIN
    old = now_ms - 100 * MIN  # outside the 1h window, inside the 2h window

    def task(task_id, state, created, **kw):
        row = {
            "task_id": task_id,
            "skill": "test-skill",
            "state": state,
            "created_at": created,
            "bounty_axm": 1.0,
            "poster_id": None,
            "assigned_to": None,
        }
        row.update(kw)
        fabric.tasks[task_id] = row

    def bid(bid_id, task_id, agent_id, amount, received):
        fabric.bids[bid_id] = {
            "bid_id": bid_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "bid_axm": amount,
            "received_at": received,
            "revealed": True,
        }

    task("t1", "settled", t, winning_bid_axm=10.0, assigned_to="agent-a",
         poster_id="poster-x", settled_at=now_ms - 5 * MIN)
    task("t2", "settled", t, winning_bid_axm=20.0, assigned_to="agent-b",
         poster_id="poster-x", settled_at=now_ms - 10 * MIN)
    task("t3", "expired", t, poster_id="poster-y")
    task("t4", "open", t, poster_id="poster-y")
    task("t6", "failed", t, poster_id="poster-z")
    task("t5", "settled", old, winning_bid_axm=50.0, assigned_to="agent-e",
         poster_id="poster-old", settled_at=old)

    bid("b1", "t1", "agent-a", 10.0, t)
    bid("b2", "t1", "agent-b", 9.0, t)
    bid("b3", "t1", "agent-c", 8.0, t)
    bid("b4", "t2", "agent-b", 20.0, t)
    bid("b5", "t2", "agent-a", 18.0, t)
    bid("b6", "t3", "agent-d", 5.0, t)
    bid("b7", "t5", "agent-e", 50.0, old)


def _seed_fees(ledger_path: Path):
    from sincor2.treasury_settlement import record_platform_fee_inflow

    # The honest 5% fee entries the endpoint must pick up.
    assert record_platform_fee_inflow(
        fee_amount=0.5, asset="AXM", source="a2a_settlement", task_id="t1")
    assert record_platform_fee_inflow(
        fee_amount=1.0, asset="AXM", source="a2a_settlement", task_id="t2")

    # Decoys — must NOT appear in the figures:
    # 1. projected (not realized) fee
    from sincor2.treasury_inflow import record_inflow
    record_inflow(999.0, asset="AXM", source="a2a_settlement",
                  note="platform fee task=tX", projected=True)
    # 2. non-AXM fee
    record_inflow(1.0, asset="USDC", source="a2a_settlement",
                  note="platform fee task=tX", projected=False)
    # 3. realized AXM inflow that is NOT a platform-fee entry
    record_inflow(100.0, asset="AXM", source="defi_swarms",
                  note="swap inflow", projected=False)
    # 4. a real fee entry, but outside the 1h window (100 min ago;
    #    inside the 2h window, so the window parameter is exercised)
    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=100)).isoformat()
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": old_ts, "amount": 2.5, "asset": "AXM",
            "source": "a2a_settlement", "usd_estimate": 2.5,
            "tx_hash": None, "note": "platform fee task=t5", "projected": False,
        }) + "\n")


@pytest.fixture()
def app(tmp_path, monkeypatch):
    ledger = tmp_path / "fees.jsonl"
    import sincor2.treasury_inflow as inflow_mod

    monkeypatch.setattr(inflow_mod, "_LEDGER_PATH", ledger)

    from sincor2.a2a_inbound import _now_ms

    now_ms = _now_ms()
    _seed_store(now_ms)
    _seed_fees(ledger)

    from flask import Flask
    from sincor2.a2a_inbound_ext import mount

    flask_app = Flask("activity_test")
    flask_app.config["TESTING"] = True
    assert mount(flask_app) is True
    flask_app.test_client()  # ensure app boots
    return flask_app


def _get(flask_app, path):
    resp = flask_app.test_client().get(path)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:400]
    return resp.get_json()


def test_activity_matches_seeded_ground_truth(app):
    data = _get(app, "/v1/a2a/activity")

    assert data["disclaimer"] == (
        "Marketplace ledger activity; not onchain trading volume.")
    # "not onchain trading volume" — the endpoint must never wear that label.
    body = json.dumps(data).lower()
    assert "trading volume" not in body.replace(
        "not onchain trading volume", "")

    aph = data["auctions_per_hour"]
    assert aph["value"] == 5.0
    assert aph["source"] == "ledger"
    assert aph["auctions_opened"] == 5

    vol = data["bid_volume_axm"]
    assert vol["value"] == 70.0
    assert vol["source"] == "ledger"
    assert vol["bids"] == 6

    rate = data["settlement_rate"]
    assert rate["value"] == 0.5
    assert rate["source"] == "ledger"
    assert rate["settled"] == 2
    assert rate["closed"] == 4

    fees = data["fees_to_treasury_axm"]
    assert fees["value"] == 1.5
    assert fees["source"] == "ledger"
    assert fees["fee_events"] == 2

    active = data["active_agents"]
    assert active["value"] == 7
    assert active["source"] == "ledger"


def test_activity_rolling_window_2h(app):
    data = _get(app, "/v1/a2a/activity?window_hours=2")
    assert data["auctions_per_hour"]["value"] == 3.0  # 6 opened / 2h
    assert data["auctions_per_hour"]["auctions_opened"] == 6
    assert data["bid_volume_axm"]["value"] == 120.0
    assert data["settlement_rate"]["value"] == 0.6  # 3 settled / 5 closed
    assert data["settlement_rate"]["settled"] == 3
    assert data["settlement_rate"]["closed"] == 5
    assert data["fees_to_treasury_axm"]["value"] == 4.0
    assert data["fees_to_treasury_axm"]["fee_events"] == 3
    assert data["active_agents"]["value"] == 9


def test_activity_empty_state(tmp_path, monkeypatch):
    ledger = tmp_path / "empty.jsonl"
    ledger.write_text("", encoding="utf-8")
    import sincor2.treasury_inflow as inflow_mod

    monkeypatch.setattr(inflow_mod, "_LEDGER_PATH", ledger)

    from sincor2.a2a_inbound import reset_fabric

    reset_fabric()  # clean, empty store

    from flask import Flask
    from sincor2.a2a_inbound_ext import mount

    flask_app = Flask("activity_empty_test")
    flask_app.config["TESTING"] = True
    mount(flask_app)
    # mount() seeds 12 probation listings into an empty store; the empty
    # state is what comes AFTER clearing — reset so the route reads nothing.
    from sincor2.a2a_inbound import reset_fabric

    reset_fabric()
    data = _get(flask_app, "/v1/a2a/activity")

    assert data["disclaimer"] == (
        "Marketplace ledger activity; not onchain trading volume.")
    assert data["auctions_per_hour"]["value"] == 0.0
    assert data["auctions_per_hour"]["source"] == "ledger"
    assert data["bid_volume_axm"]["value"] == 0.0
    assert data["bid_volume_axm"]["source"] == "ledger"
    assert data["settlement_rate"]["value"] == 0.0
    assert data["settlement_rate"]["source"] == "ledger"
    assert data["settlement_rate"]["settled"] == 0
    assert data["settlement_rate"]["closed"] == 0
    assert data["fees_to_treasury_axm"]["value"] == 0.0
    assert data["fees_to_treasury_axm"]["source"] == "ledger"
    assert data["fees_to_treasury_axm"]["fee_events"] == 0
    assert data["active_agents"]["value"] == 0
    assert data["active_agents"]["source"] == "ledger"
