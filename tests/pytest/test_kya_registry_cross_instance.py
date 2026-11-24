"""W-205 — Live KYA registry (sincor2.kya_registry) cross-instance lost updates.

The registry is module-global state persisted as ONE whole-registry JSON
blob (PersistentStore kv -> Redis -> file, first success wins). Two
processes/instances each load once; a stale instance's save() rewrites
the whole blob from its stale snapshot, silently erasing registrations
and revocations a peer persisted in the meantime.

These tests collapse the multi-process shape in-process: a "stale peer"
is the same module with its global state snapshot-restored to what it
held before the other instance acted (the file backend is forced so the
persisted blob is hermetic per test). save() must reload the latest
persisted registry under a cross-process flock and MERGE per record
(keyed by kya_id/agent_id; revocations and tombstones are sticky;
per-record newest updated_at wins) instead of blindly overwriting.
"""
from __future__ import annotations

import copy

import pytest

from sincor2 import kya_registry as kya

W1 = "0x1111111111111111111111111111111111111111"
W2 = "0x2222222222222222222222222222222222222222"
W3 = "0x3333333333333333333333333333333333333333"


def _agent(agent_id: str, wallet: str) -> dict:
    return {
        "agent_id": agent_id,
        "name": agent_id,
        "description": "cross-instance test agent",
        "version": "0.1.0",
        "skills": [{"id": "lead-enrichment", "name": "Lead Enrichment"}],
        "rpc_callback": "https://getsincor.com/api/a2a",
        "wallet": wallet,
        "last_heartbeat": 0,
    }


@pytest.fixture(autouse=True)
def _file_backend(tmp_path, monkeypatch):
    """Force the file backend so the persisted blob is per-test hermetic."""
    monkeypatch.setattr(kya, "_sqlite", lambda: None)
    monkeypatch.setattr(kya, "_redis", lambda: None)
    monkeypatch.setenv("KYA_STORE_PATH", str(tmp_path / "kya_records.json"))
    kya.reset()
    yield
    kya.reset()


def _snapshot_state():
    with kya._LOCK:
        return (
            copy.deepcopy(list(kya._STORE.values())),
            copy.deepcopy(list(kya._TOMBSTONES.values())),
        )


def _restore_state(snap) -> None:
    """Make the module look like a peer whose snapshot was taken earlier."""
    records, tombstones = snap
    kya.reset()
    kya._ingest(copy.deepcopy(records))
    kya._ingest_tombstones(copy.deepcopy(tombstones))


def _fresh_loaded() -> dict:
    """Simulate a brand-new process: drop memory, load from persistence."""
    kya.reset()
    kya.load()
    with kya._LOCK:
        return {r["agent_id"]: copy.deepcopy(r) for r in kya._STORE.values()}


def test_two_instances_register_different_agents_both_survive():
    empty = _snapshot_state()
    kya.list_from_inbound(_agent("agent-a", W1))

    # Stale peer: its snapshot predates agent-a's registration entirely.
    _restore_state(empty)
    kya.list_from_inbound(_agent("agent-b", W2))

    fresh = _fresh_loaded()
    assert "agent-a" in fresh, f"peer's registration erased by stale save: {sorted(fresh)}"
    assert "agent-b" in fresh, f"own registration missing: {sorted(fresh)}"


def test_revocation_survives_stale_peer_save_and_is_not_resurrected():
    rec_a = kya.list_from_inbound(_agent("agent-a", W1))
    kya.list_from_inbound(_agent("agent-b", W2))

    stale = _snapshot_state()  # peer snapshot: agent-a live, no tombstone

    kya.revoke(rec_a["kya_id"], "compromised")
    # Sanity: the revocation really is persisted before the stale peer acts.
    assert _fresh_loaded()["agent-a"]["revoked"] is True

    # Stale peer saves an unrelated registration from its stale snapshot.
    _restore_state(stale)
    kya.list_from_inbound(_agent("agent-c", W3))

    fresh = _fresh_loaded()
    assert fresh["agent-a"]["revoked"] is True, "stale save resurrected a revoked agent"
    assert fresh["agent-a"]["status"] == "revoked"
    assert "agent-b" in fresh, "unrelated peer record lost"
    assert "agent-c" in fresh, "stale peer's own registration lost"
    # The tombstone written by revoke() must survive the stale save too.
    assert kya.is_wallet_tombstoned(W1), "tombstone erased by stale save"


def test_two_instances_update_same_agent_newest_wins_no_record_lost():
    kya.list_from_inbound(_agent("agent-a", W1))

    # Peer updates the record and persists the NEWER version.
    with kya._LOCK:
        rec = kya.get_by_agent("agent-a")
        rec["name"] = "Newer Name"
        rec["updated_at"] = 2_000_000_000_000
    kya.save()

    # Stale instance holds an OLDER version of the same record and saves.
    records, tombstones = _snapshot_state()
    for r in records:
        if r["agent_id"] == "agent-a":
            r["name"] = "Older Name"
            r["updated_at"] = 1_000_000_000_000
    _restore_state((records, tombstones))
    kya.save()

    fresh = _fresh_loaded()
    assert list(fresh) == ["agent-a"], f"record duplicated/lost: {sorted(fresh)}"
    assert fresh["agent-a"]["name"] == "Newer Name", (
        f"stale save overwrote the newer update: {fresh['agent-a']['name']}"
    )
    assert fresh["agent-a"]["updated_at"] == 2_000_000_000_000
