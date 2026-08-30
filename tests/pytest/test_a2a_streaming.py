"""Real A2A message/stream SSE — token chunks, not a single dumped blob."""
from __future__ import annotations

import json


def _sse_payloads(response) -> list:
    text = response.get_data(as_text=True)
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def test_stream_is_event_stream(client):
    resp = client.post(
        "/api/a2a",
        json={
            "jsonrpc": "2.0",
            "id": 42,
            "method": "message/stream",
            "params": {
                "skillId": "lead-enrichment",
                "callerId": "stream-caller",
                "message": {
                    "role": "user",
                    "parts": [{"text": "Stream this enrichment for Acme"}],
                },
            },
        },
    )
    assert resp.status_code == 200
    assert "text/event-stream" in (resp.mimetype or resp.content_type)


def test_stream_emits_status_then_token_chunks(client):
    resp = client.post(
        "/api/a2a",
        json={
            "jsonrpc": "2.0",
            "id": "stream-1",
            "method": "message/stream",
            "params": {
                "skillId": "content-blog",
                "callerId": "stream-caller-2",
                "message": {
                    "role": "user",
                    "parts": [{"text": "Write a short outline about CRM sync automation for sales ops teams"}],
                },
            },
        },
    )
    events = _sse_payloads(resp)
    assert len(events) >= 4

    kinds = []
    for ev in events:
        result = ev.get("result") or {}
        kinds.append(result.get("kind") or "")

    assert "status-update" in kinds
    assert "artifact-update" in kinds

    statuses = [
        (ev.get("result") or {}).get("status", {}).get("state")
        or (ev.get("result") or {}).get("taskStatus", {}).get("status", {}).get("state")
        for ev in events
        if (ev.get("result") or {}).get("kind") == "status-update"
        or (ev.get("result") or {}).get("taskStatus")
    ]
    assert "submitted" in statuses
    assert "working" in statuses
    assert "completed" in statuses

    artifacts = [
        ev for ev in events
        if (ev.get("result") or {}).get("kind") == "artifact-update"
        or (ev.get("result") or {}).get("taskArtifact")
    ]
    # Must be more than a single blob — that's the whole bug.
    token_events = [a for a in artifacts if not (a.get("result") or {}).get("lastChunk")]
    if not token_events:
        token_events = [
            a for a in artifacts
            if not ((a.get("result") or {}).get("taskArtifact") or {}).get("lastChunk")
        ]
    assert len(token_events) >= 2, f"expected token chunks, got {len(token_events)}: {artifacts!r}"

    last = artifacts[-1].get("result") or {}
    assert last.get("lastChunk") is True or (last.get("taskArtifact") or {}).get("lastChunk") is True

    finals = [
        (ev.get("result") or {}).get("final")
        or ((ev.get("result") or {}).get("taskStatus") or {}).get("final")
        for ev in events
    ]
    assert True in finals


def test_stream_error_on_empty_input(client):
    resp = client.post(
        "/api/a2a",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/stream",
            "params": {
                "skillId": "lead-enrichment",
                "message": {"role": "user", "parts": [{"text": ""}]},
            },
        },
    )
    events = _sse_payloads(resp)
    assert events
    assert "error" in events[0]


def test_dispatch_stream_chunks_stub(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from sincor2.a2a_integration import A2ATask, TaskState, _dispatch_to_swarm_stream, _now

    task = A2ATask(
        id="t-stream",
        context_id="c-stream",
        skill_id="lead-enrichment",
        input_text="Hello from the streaming unit test — please chunk this generously.",
        caller_id="unit",
        state=TaskState.WORKING,
        created_at=_now(),
        updated_at=_now(),
    )
    pieces = list(_dispatch_to_swarm_stream(task))
    assert len(pieces) >= 2
    assert any("lead-enrichment" in p or "SINCOR" in p or "Hello" in p for p in pieces)
