"""Proof wall — real-time upgrade (spec 2026-11-15).

Covers:
* proof_events store: dedupe by event_id, totals split by origin,
  validation refusals, durability across instances, fail-closed on a
  corrupt store file.
* Settle emission (a2a_integration._finalize_a2a_task): a paid,
  settle-final task emits exactly one marketplace event; simulated,
  free, unpaid, and failed tasks emit nothing.
* POST /api/proof-wall/ingest: HMAC gate, CONFIRMED+SUCCESS claim
  gate, server-side receipt re-verification via the stubbed RPC seam
  (good receipt recorded; failed / missing / mismatched receipts
  rejected), replay is a no-op.
* Rendering: GET /x402-live shows the spec empty-state sentence with
  an empty store, renders store events with origin labels and split
  totals when populated (snapshot tallies never blended), degrades —
  never 500s — on a corrupt store; GET /x402-live/events.json feed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest

from sincor2 import a2a_integration as ai
from sincor2 import proof_events

TX_A = "0x" + "aa" * 32
TX_B = "0x" + "bb" * 32
TX_C = "0x" + "cc" * 32
INGEST_SECRET = "test-ingest-secret"
EMPTY_STATE = (
    "No verified settled activity yet — this wall only displays "
    "events with an on-chain record."
)


def _trade_event(event_id="moebiusx:trade-107", tx_hash=TX_A, **over):
    event = {
        "event_id": event_id,
        "source": "moebiusx_v3.1",
        "origin": "sincor_agent",
        "label": "MoebiusX trade-107",
        "detail": "SELL settled on Base — $8.24 USDC",
        "amount": "8.24",
        "asset": "USDC",
        "tx_hash": tx_hash,
        "occurred_at": "2026-11-14T15:27:09Z",
    }
    event.update(over)
    return event


def _task_event(event_id="task:tsk_1", tx_hash=TX_B, **over):
    event = {
        "event_id": event_id,
        "source": "marketplace",
        "origin": "marketplace_task",
        "label": "Task tsk_1",
        "detail": "Settled on Base — 2.0000 AXM",
        "amount": "2.0000",
        "asset": "AXM",
        "tx_hash": tx_hash,
        "occurred_at": "2026-11-15T00:00:00Z",
    }
    event.update(over)
    return event


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Every test gets a throwaway proof-event store (and data dir)."""
    monkeypatch.setenv("SINCOR_PROOF_EVENTS_PATH",
                       str(tmp_path / "proof_events.jsonl"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    proof_events.reset_default_store()
    yield tmp_path
    proof_events.reset_default_store()


@pytest.fixture(scope="module")
def mvp_app():
    from sincor2.mvp_app import app as flask_app

    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture(scope="module")
def mvp_client(mvp_app):
    return mvp_app.test_client()


# ── store ───────────────────────────────────────────────────────────────────

def test_store_record_and_dedupe():
    assert proof_events.record_event(_trade_event()) is True
    assert proof_events.record_event(_trade_event()) is False  # replay
    assert proof_events.totals() == {
        "total": 1, "by_origin": {
            "external_agent": 0, "marketplace_task": 0, "sincor_agent": 1}}
    assert len(proof_events.list_events()) == 1


def test_store_totals_split_by_origin():
    proof_events.record_event(_trade_event("moebiusx:trade-1", TX_A))
    proof_events.record_event(_trade_event("moebiusx:trade-2", TX_B))
    proof_events.record_event(_task_event())
    totals = proof_events.totals()
    assert totals["total"] == 3
    assert totals["by_origin"]["sincor_agent"] == 2
    assert totals["by_origin"]["marketplace_task"] == 1
    assert totals["by_origin"]["external_agent"] == 0


def test_store_rejects_invalid_events():
    with pytest.raises(ValueError):
        proof_events.record_event(_trade_event(tx_hash="0xSIMULATED123"))
    with pytest.raises(ValueError):
        proof_events.record_event(_trade_event(tx_hash="0xshort"))
    with pytest.raises(ValueError):
        proof_events.record_event(_trade_event(origin="nobody"))
    with pytest.raises(ValueError):
        proof_events.record_event(_trade_event(source="rumor"))
    with pytest.raises(ValueError):
        proof_events.record_event(_trade_event(label=""))
    assert proof_events.totals()["total"] == 0


def test_store_persists_across_instances(isolated_store):
    path = isolated_store / "proof_events.jsonl"
    proof_events.record_event(_trade_event())
    fresh = proof_events.ProofEventStore(path)
    assert fresh.totals()["total"] == 1
    assert fresh.list_events()[0]["event_id"] == "moebiusx:trade-107"
    # The fresh instance dedupes against what is already on disk.
    assert fresh.record_event(_trade_event()) is False


def test_store_corrupt_file_fails_closed(isolated_store):
    path = isolated_store / "proof_events.jsonl"
    proof_events.record_event(_trade_event())
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"event_id": "broken", garbage\n')
    proof_events.reset_default_store()
    with pytest.raises(proof_events.ProofStoreError):
        proof_events.list_events()
    with pytest.raises(proof_events.ProofStoreError):
        proof_events.totals()


def test_list_events_newest_first_with_origin_labels():
    proof_events.record_event(_trade_event("moebiusx:trade-1", TX_A))
    proof_events.record_event(_task_event(tx_hash=TX_C))
    events = proof_events.list_events()
    assert [e["event_id"] for e in events] == [
        "task:tsk_1", "moebiusx:trade-1"]
    assert events[0]["origin_label"] == "Marketplace task"
    assert events[1]["origin_label"] == "SINCOR agent"
    assert len(proof_events.list_events(limit=1)) == 1


# ── settle emission ─────────────────────────────────────────────────────────

def _a2a_task(**over):
    fields = dict(
        id="tsk_" + uuid.uuid4().hex[:10],
        context_id="ctx-1",
        skill_id="lead-enrichment",
        input_text="do the thing",
        caller_id="caller-1",
        state=ai.TaskState.WORKING,
        created_at=ai._now(),
        updated_at=ai._now(),
        metadata={},
    )
    fields.update(over)
    return ai.A2ATask(**fields)


def test_paid_task_finalize_emits_marketplace_event():
    task = _a2a_task(axm_paid=2 * 10**18, tx_hash=TX_A)
    result = ai._finalize_a2a_task(task, "the output", None)
    assert result["state"] == "completed"
    events = proof_events.list_events()
    assert len(events) == 1
    event = events[0]
    assert event["event_id"] == f"task:{task.id}"
    assert event["source"] == "marketplace"
    assert event["origin"] == "marketplace_task"
    assert event["origin_label"] == "Marketplace task"
    assert event["asset"] == "AXM"
    assert event["amount"] == "2.0000"
    assert event["tx_hash"] == TX_A


def test_free_task_emits_nothing():
    task = _a2a_task(axm_paid=2 * 10**18, tx_hash=TX_A,
                     metadata={"free_call": True})
    result = ai._finalize_a2a_task(task, "the output", None)
    assert result["state"] == "completed"
    assert proof_events.totals()["total"] == 0


def test_simulated_tx_emits_nothing():
    task = _a2a_task(axm_paid=2 * 10**18,
                     tx_hash="0xSIMULATED" + "ab" * 27)
    result = ai._finalize_a2a_task(task, "the output", None)
    assert result["state"] == "completed"
    assert proof_events.totals()["total"] == 0


def test_unpaid_task_emits_nothing():
    task = _a2a_task(axm_paid=0, tx_hash=None)
    result = ai._finalize_a2a_task(task, "the output", None)
    assert result["state"] == "completed"
    assert proof_events.totals()["total"] == 0


def test_failed_task_emits_nothing():
    task = _a2a_task(axm_paid=2 * 10**18, tx_hash=TX_A)
    result = ai._finalize_a2a_task(task, None, "swarm exploded")
    assert result["state"] == "failed"
    assert proof_events.totals()["total"] == 0


# ── ingest route ────────────────────────────────────────────────────────────

def _ingest_body(**over):
    body = _trade_event()
    body.update({"status": "CONFIRMED", "outcome": "SUCCESS"})
    body.update(over)
    return body


def _post_ingest(client, body, secret=INGEST_SECRET, signature=None):
    raw = json.dumps(body).encode()
    if signature is None:
        signature = hmac.new(
            secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/api/proof-wall/ingest", data=raw,
        content_type="application/json",
        headers={"X-Sincor-Signature": signature})


@pytest.fixture
def ingest_env(monkeypatch):
    monkeypatch.setenv(ai.PROOF_WALL_INGEST_SECRET_ENV, INGEST_SECRET)


def _stub_receipt(monkeypatch, receipt):
    monkeypatch.setattr(ai, "_proof_wall_fetch_receipt",
                        lambda tx_hash: receipt)


def test_ingest_accepts_receipt_verified_settlement(
        mvp_client, ingest_env, monkeypatch):
    _stub_receipt(monkeypatch, {"status": "0x1", "transactionHash": TX_A,
                                "blockNumber": "0x64"})
    resp = _post_ingest(mvp_client, _ingest_body())
    assert resp.status_code == 201
    assert resp.get_json()["recorded"] is True
    events = proof_events.list_events()
    assert len(events) == 1
    assert events[0]["event_id"] == "moebiusx:trade-107"
    assert events[0]["origin_label"] == "SINCOR agent"


def test_ingest_replay_is_noop(mvp_client, ingest_env, monkeypatch):
    _stub_receipt(monkeypatch, {"status": "0x1", "transactionHash": TX_A})
    assert _post_ingest(mvp_client, _ingest_body()).status_code == 201
    resp = _post_ingest(mvp_client, _ingest_body())
    assert resp.status_code == 200
    assert resp.get_json()["duplicate"] is True
    assert proof_events.totals()["total"] == 1


def test_ingest_rejects_failed_receipt(mvp_client, ingest_env, monkeypatch):
    _stub_receipt(monkeypatch, {"status": "0x0", "transactionHash": TX_A})
    resp = _post_ingest(mvp_client, _ingest_body())
    assert resp.status_code == 400
    assert proof_events.totals()["total"] == 0


def test_ingest_rejects_missing_receipt(mvp_client, ingest_env, monkeypatch):
    _stub_receipt(monkeypatch, None)
    resp = _post_ingest(mvp_client, _ingest_body())
    assert resp.status_code == 400
    assert proof_events.totals()["total"] == 0


def test_ingest_rejects_mismatched_receipt(mvp_client, ingest_env,
                                           monkeypatch):
    _stub_receipt(monkeypatch, {"status": "0x1", "transactionHash": TX_B})
    resp = _post_ingest(mvp_client, _ingest_body())
    assert resp.status_code == 400
    assert proof_events.totals()["total"] == 0


def test_ingest_rejects_bad_signature(mvp_client, ingest_env, monkeypatch):
    _stub_receipt(monkeypatch, {"status": "0x1", "transactionHash": TX_A})
    resp = _post_ingest(mvp_client, _ingest_body(), signature="0" * 64)
    assert resp.status_code == 401
    assert proof_events.totals()["total"] == 0


def test_ingest_disabled_without_secret(mvp_client, monkeypatch):
    monkeypatch.delenv(ai.PROOF_WALL_INGEST_SECRET_ENV, raising=False)
    _stub_receipt(monkeypatch, {"status": "0x1", "transactionHash": TX_A})
    resp = _post_ingest(mvp_client, _ingest_body())
    assert resp.status_code == 503
    assert proof_events.totals()["total"] == 0


def test_ingest_rejects_unconfirmed_claim(mvp_client, ingest_env,
                                          monkeypatch):
    _stub_receipt(monkeypatch, {"status": "0x1", "transactionHash": TX_A})
    resp = _post_ingest(mvp_client, _ingest_body(status="VALIDATION_FAILED"))
    assert resp.status_code == 400
    assert proof_events.totals()["total"] == 0


def test_ingest_rejects_marketplace_source(mvp_client, ingest_env,
                                           monkeypatch):
    _stub_receipt(monkeypatch, {"status": "0x1", "transactionHash": TX_B})
    body = _ingest_body(**_task_event())
    resp = _post_ingest(mvp_client, body)
    assert resp.status_code == 400
    assert proof_events.totals()["total"] == 0


# ── rendering ───────────────────────────────────────────────────────────────

def test_x402_live_empty_state(mvp_client):
    resp = mvp_client.get("/x402-live")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert EMPTY_STATE in html
    # Snapshot section is untouched.
    assert "81 confirmed trades" in html
    assert "Snapshot as of 2026-11-12" in html


def test_x402_live_renders_store_events_over_snapshot(mvp_client):
    proof_events.record_event(_trade_event())
    proof_events.record_event(_task_event())
    resp = mvp_client.get("/x402-live")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # Store events render with origin labels and split totals.
    assert "Live activity" in html
    assert "SINCOR agent" in html
    assert "Marketplace task" in html
    assert "MoebiusX trade-107" in html
    assert "2 verified events" in html
    # The rendered empty-state block is gone (the sentence itself also
    # lives in the polling script's fallback string — that is not the
    # server-rendered empty state).
    assert 'class="pw-empty"' not in html
    # Snapshot tallies are NOT blended with event counts.
    assert "81 confirmed trades" in html


def test_events_json_feed(mvp_client):
    resp = mvp_client.get("/x402-live/events.json")
    assert resp.status_code == 200
    feed = resp.get_json()
    assert feed["events"] == []
    assert feed["event_totals"]["total"] == 0
    assert feed["events_available"] is True

    proof_events.record_event(_trade_event())
    feed = mvp_client.get("/x402-live/events.json").get_json()
    assert feed["event_totals"]["total"] == 1
    assert feed["event_totals"]["by_origin"]["sincor_agent"] == 1
    assert feed["events"][0]["origin_label"] == "SINCOR agent"


def test_x402_live_degrades_on_corrupt_store(mvp_client, isolated_store):
    path = isolated_store / "proof_events.jsonl"
    path.write_text('{"event_id": "broken", garbage\n', encoding="utf-8")
    proof_events.reset_default_store()
    resp = mvp_client.get("/x402-live")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert EMPTY_STATE in html
    assert "81 confirmed trades" in html
    feed = mvp_client.get("/x402-live/events.json").get_json()
    assert feed["events_available"] is False
    assert feed["events"] == []
