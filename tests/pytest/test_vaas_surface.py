"""Path 49 — VaaS external surface tests.

Core claim (parity): the HTTP surface returns the SAME verdicts as
direct calls to the internal guards (axm_antigaming.check_bid_eligibility
/ check_self_bid) on identical inputs and identical store state.
Also: auth required, one metering entry per check, no internal
threshold/weight/count fields in any payload, read-only (no quota
consumed), and pricing/health are public and sellable.
"""
from __future__ import annotations

import pytest
from flask import Flask

from sincor2 import axm_antigaming as ag
from sincor2 import vaas_blueprint as vaas

KEY = "vaas-test-key-0123456789abcdef"
HEADERS = {"X-Api-Key": KEY}

FORBIDDEN_KEYS = {
    "cap", "caps", "threshold", "thresholds", "weight", "weights",
    "window", "window_s", "max_bids", "max_wins", "bids_in_window",
    "wins_in_window", "win_streak", "pair_streak", "pair_wins",
    "streak", "limit", "score",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VAAS_API_KEYS", KEY)
    monkeypatch.setenv("AXM_AG_MAX_BIDS_PER_HOUR", "1000")
    monkeypatch.setenv("AXM_AG_MAX_WINS_PER_HOUR", "1000")
    monkeypatch.setenv("AXM_AG_MAX_CONSECUTIVE_WINS", "3")
    monkeypatch.setenv("AXM_AG_MAX_PAIR_CONSECUTIVE", "2")
    monkeypatch.setenv("AXM_AG_MAX_PAIR_WINS_PER_WINDOW", "2")
    ag.reset_antigaming_store(path=str(tmp_path / "antigaming.json"))
    vaas.reset_vaas_meter()
    app = Flask(__name__)
    app.register_blueprint(vaas.vaas_bp)
    return app.test_client()


def _direct_verdict(task, participant):
    """Ground truth straight from the internal enforcement functions."""
    try:
        ag.check_bid_eligibility(task, participant)
    except ag.AnonymousBountyError:
        return ("ineligible", "anonymous_bounty")
    except ag.SelfBidError:
        return ("ineligible", "self_bid")
    except ag.ParticipationCapError:
        return ("ineligible", "participation_cap")
    except ag.CollusionCapError:
        return ("ineligible", "collusion_cap")
    except ag.GuardUnavailableError:
        return ("unknown", "guard_unavailable")
    return ("eligible", "none")


def _all_keys(obj):
    keys = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(str(k).lower())
            keys |= _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            keys |= _all_keys(v)
    return keys


CASES = [
    # (task, participant) — honest, self-bid, anonymous bounty, anon no bounty
    ({"task_id": "t1", "poster_id": "poster1", "bounty_axm": 1.0}, "bidder1"),
    ({"task_id": "t2", "poster_id": "poster1", "bounty_axm": 1.0}, "poster1"),
    ({"task_id": "t3", "poster_id": None, "bounty_axm": 5.0}, "bidder1"),
    ({"task_id": "t4", "poster_id": None, "bounty_axm": 0}, "bidder1"),
]


@pytest.mark.parametrize("task,participant", CASES)
def test_parity_single_check(client, task, participant):
    expected = _direct_verdict(task, participant)
    resp = client.post("/v1/vaas/check", json={"task": task, "participant_id": participant},
                       headers=HEADERS)
    assert resp.status_code == 200
    body = resp.get_json()
    assert (body["verdict"], body["reason_code"]) == expected
    assert body["eligible"] is (expected[0] == "eligible")


def test_parity_collusion_cap_after_recorded_wins(client):
    task = {"task_id": "tc", "poster_id": "posterX", "bounty_axm": 1.0}
    # Two pair wins in-window trips the pair wins-per-window cap (2).
    ag.record_auction_close("posterX", "winner1", task_id="a")
    ag.record_auction_close("posterY", "other", task_id="b")  # streak reset spacer is NOT enough: window cap persists
    ag.record_auction_close("posterX", "winner1", task_id="c")
    expected = _direct_verdict(task, "winner1")
    assert expected[0] == "ineligible"  # ground truth really is capped
    resp = client.post("/v1/vaas/check", json={"task": task, "participant_id": "winner1"},
                       headers=HEADERS)
    body = resp.get_json()
    assert (body["verdict"], body["reason_code"]) == expected


def test_parity_campaign_batch(client):
    task = {"task_id": "tb", "poster_id": "poster1", "bounty_axm": 1.0}
    participants = ["poster1", "alice", "bob"]
    expected = {p: _direct_verdict(task, p) for p in participants}
    resp = client.post("/v1/vaas/campaign/check",
                       json={"campaign_id": "camp1", "task": task, "participants": participants},
                       headers=HEADERS)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["checks"] == 3
    for row in body["results"]:
        assert (row["verdict"], row["reason_code"]) == expected[row["participant_id"]]
    assert body["summary"]["eligible"] + body["summary"]["ineligible"] == 3


def test_auth_required(client):
    task, participant = CASES[0]
    assert client.post("/v1/vaas/check", json={"task": task, "participant_id": participant}).status_code == 401
    assert client.post("/v1/vaas/check", json={"task": task, "participant_id": participant},
                       headers={"X-Api-Key": "wrong-key-xxxxxxxx"}).status_code == 401
    assert client.post("/v1/vaas/campaign/check",
                       json={"task": task, "participants": [participant]}).status_code == 401
    # Bearer form works too.
    resp = client.post("/v1/vaas/check", json={"task": task, "participant_id": participant},
                       headers={"Authorization": f"Bearer {KEY}"})
    assert resp.status_code == 200


def test_metering_one_entry_per_check(client):
    task, _ = CASES[0]
    client.post("/v1/vaas/check", json={"task": task, "participant_id": "alice"}, headers=HEADERS)
    assert len(vaas.vaas_meter().entries()) == 1
    client.post("/v1/vaas/campaign/check",
                json={"campaign_id": "c9", "task": task, "participants": ["a", "b", "c"]},
                headers=HEADERS)
    entries = vaas.vaas_meter().entries()
    assert len(entries) == 4  # 1 single + 3 batch participants
    assert all(e["type"] == "vaas_check" for e in entries)
    assert all(e["price_usd"] == vaas.VAAS_PRICE_PER_CHECK_USD for e in entries)
    assert all(e["caller"].startswith("key_") and KEY not in str(e) for e in entries)
    # Unauthenticated attempts are not metered.
    client.post("/v1/vaas/check", json={"task": task, "participant_id": "alice"})
    assert len(vaas.vaas_meter().entries()) == 4


def test_no_internal_thresholds_leak(client):
    task = {"task_id": "t2", "poster_id": "poster1", "bounty_axm": 1.0}
    for url, payload in [
        ("/v1/vaas/check", {"task": task, "participant_id": "poster1"}),
        ("/v1/vaas/campaign/check", {"task": task, "participants": ["poster1", "alice"]}),
    ]:
        body = client.post(url, json=payload, headers=HEADERS).get_json()
        assert not (_all_keys(body) & FORBIDDEN_KEYS), _all_keys(body)
        # Internal exception messages embed cap values (e.g. "cap 50",
        # "wins_in_window=..."); no verdict string may carry a number
        # or an internal detail key.
        allowed_reasons = {"none", "self_bid", "anonymous_bounty",
                           "participation_cap", "collusion_cap",
                           "guard_unavailable"}
        rows = body.get("results") or [body]
        for row in rows:
            assert row["reason_code"] in allowed_reasons
            assert not any(ch.isdigit() for ch in row["reason_code"])
            assert set(row) <= {"participant_id", "eligible", "verdict",
                                "reason_code", "risk_band",
                                "price_usd", "currency"}


def test_check_is_read_only_no_quota_consumed(client):
    store = ag.antigaming_store()
    task, participant = CASES[0]
    before = store.bids_in_window(participant)
    for _ in range(5):
        client.post("/v1/vaas/check", json={"task": task, "participant_id": participant},
                    headers=HEADERS)
    assert store.bids_in_window(participant) == before
    assert store.wins_in_window(participant) == 0


def test_public_pricing_and_health(client):
    health = client.get("/v1/vaas/health")
    assert health.status_code == 200 and health.get_json()["ok"] is True
    pricing = client.get("/v1/vaas/pricing")
    assert pricing.status_code == 200
    body = pricing.get_json()
    assert body["price_per_check_usd"] == vaas.VAAS_PRICE_PER_CHECK_USD
    assert not (_all_keys(body) & FORBIDDEN_KEYS)


def test_validation_errors(client):
    assert client.post("/v1/vaas/check", json={"participant_id": "x"}, headers=HEADERS).status_code == 400
    assert client.post("/v1/vaas/check", json={"task": {"task_id": "t"}}, headers=HEADERS).status_code == 400
    assert client.post("/v1/vaas/campaign/check", json={"task": {"task_id": "t"}, "participants": []},
                       headers=HEADERS).status_code == 400
