"""Regression test: _PENDING confirmation store prunes expired entries.

Hole: _issue_confirmation() added entries with a 300s TTL, but expired
entries were never removed — only _confirm_and_execute() popped entries,
and only on confirmation attempts. Unconfirmed expired entries accumulated
forever (slow memory exhaustion via repeated unconfirmed write-tool calls).

Fix: _issue_confirmation() prunes expired entries before inserting.
"""
from __future__ import annotations

import time

import sincor2.mcp_server as ms


def test_pending_prunes_expired_entries():
    ms._PENDING.clear()
    try:
        for i in range(5):
            ms._issue_confirmation(
                "register_agent", "POST", "/v1/a2a/register",
                {"agent_id": f"agent-{i}"}, "summary",
            )
        assert len(ms._PENDING) == 5
        # Expire them all without confirming.
        for entry in ms._PENDING.values():
            entry["expires_at"] = time.time() - 1
        # Issuing one more must prune the 5 expired first.
        ms._issue_confirmation(
            "register_agent", "POST", "/v1/a2a/register",
            {"agent_id": "fresh"}, "summary",
        )
        assert len(ms._PENDING) == 1, (
            f"expired entries not pruned: {len(ms._PENDING)} entries remain"
        )
    finally:
        ms._PENDING.clear()


def test_pending_keeps_live_entries():
    ms._PENDING.clear()
    try:
        first = ms._issue_confirmation(
            "register_agent", "POST", "/v1/a2a/register",
            {"agent_id": "live"}, "summary",
        )
        ms._issue_confirmation(
            "submit_bid", "POST", "/v1/a2a/bids/commit",
            {"task_id": "t1"}, "summary",
        )
        # Both live entries survive the prune.
        assert len(ms._PENDING) == 2
        assert first["action_id"] in ms._PENDING
    finally:
        ms._PENDING.clear()
