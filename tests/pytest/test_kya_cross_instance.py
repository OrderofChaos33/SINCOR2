"""Cross-instance lost-update tests for KYARegistry (W-203) and
AirdropQuest (W-214).

Two instances of each class are constructed on the same store path
(separate JsonStore/LockedJsonStore objects, same file — the production
multi-process shape collapsed in-process). One instance mutates, then a
STALE second instance (snapshot taken before the first mutation) mutates
something else. On the load-once + blind-_persist code the stale save
silently erases the first instance's write; routed through
JsonStore.update() (reload-under-flock) both writes must survive.
"""
from __future__ import annotations

import json

import pytest

from sincor2.kya import airdrop_quest as aqmod
from sincor2.kya import registry as regmod
from sincor2.kya.airdrop_quest import AirdropQuest
from sincor2.kya.registry import KYARegistry, MIN_STAKE_WEI

W1 = "0x1111111111111111111111111111111111111111"
W2 = "0x2222222222222222222222222222222222222222"


def _persisted_registry(reg: KYARegistry) -> dict:
    return json.loads(reg.store.path.read_text(encoding="utf-8"))


def _persisted_quest(q: AirdropQuest) -> dict:
    return json.loads(q.store.path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# W-203: KYARegistry
# ---------------------------------------------------------------------------


def test_instances_share_path_but_not_store_objects():
    a = KYARegistry()
    b = KYARegistry()
    assert a.store is not b.store
    assert a.store.path == b.store.path
    assert a.store._locked is not b.store._locked


def test_registry_revocation_survives_stale_instance_heartbeat():
    a = KYARegistry()
    r1 = a.list_agent({"agent_id": "bot-1", "wallet": W1})
    r2 = a.list_agent({"agent_id": "bot-2", "wallet": W2})

    # B snapshots the store AFTER both listings, BEFORE the revocation.
    b = KYARegistry()
    assert b.lookup(agent_id="bot-1")["status"] == "listed"

    a.revoke(r1["kya_id"], reason="compromised")
    # Sanity: A's revocation really is on disk before B acts.
    assert _persisted_registry(a)["records"][r1["kya_id"]]["status"] == "revoked"

    # Stale B heartbeats a DIFFERENT record; its blind save would rewrite
    # bot-1 from its stale (unrevoked) snapshot.
    out = b.heartbeat("bot-2")
    assert out is not None and out["heartbeat_ok"] is True

    persisted = _persisted_registry(a)
    assert persisted["records"][r1["kya_id"]]["status"] == "revoked"
    assert persisted["records"][r1["kya_id"]]["revoked"] is True
    assert persisted["records"][r2["kya_id"]]["heartbeat_ts"] == out["heartbeat_ts"]

    fresh = KYARegistry()
    assert fresh.lookup(agent_id="bot-1")["status"] == "revoked"
    assert fresh.lookup(agent_id="bot-2")["heartbeat_ts"] == out["heartbeat_ts"]
    # B's own in-memory state is re-synced from the persisted state.
    assert b.lookup(agent_id="bot-1")["status"] == "revoked"


def test_registry_stale_bind_does_not_erase_other_listing():
    a = KYARegistry()
    a.list_agent({"agent_id": "bot-1", "wallet": W1})
    b = KYARegistry()  # stale after A's next listing
    a.list_agent({"agent_id": "bot-2", "wallet": W2})

    b.bind("bot-1", W1)

    fresh = KYARegistry()
    assert fresh.lookup(agent_id="bot-1")["status"] == "bound"
    assert fresh.lookup(agent_id="bot-2") is not None
    assert fresh.lookup(wallet=W2)["agent_id"] == "bot-2"


# ---------------------------------------------------------------------------
# W-214: AirdropQuest
# ---------------------------------------------------------------------------


def _verified_registry(monkeypatch) -> KYARegistry:
    reg = KYARegistry()
    monkeypatch.setattr(regmod, "_REG", reg)
    for agent_id, wallet in (("bot-1", W1), ("bot-2", W2)):
        listed = reg.list_agent({"agent_id": agent_id, "wallet": wallet})
        reg.bind(agent_id, wallet)
        reg.apply_stake(listed["kya_id"], str(MIN_STAKE_WEI), "0xstake")
        reg.verify(listed["kya_id"])
    return reg


def _seeded_quest() -> AirdropQuest:
    q = AirdropQuest()
    q.seed([W1, W2], source="test")
    return q


def test_airdrop_claims_from_two_stale_instances_both_survive(monkeypatch):
    _verified_registry(monkeypatch)
    _seeded_quest()

    a = AirdropQuest()
    b = AirdropQuest()  # stale snapshot: no claims yet
    assert a.claims == {} and b.claims == {}

    a.claim(W1, "bot-1")
    b.claim(W2, "bot-2")  # stale save would erase W1's claim

    persisted = _persisted_quest(a)
    keys = set(persisted["claims"])
    assert len(keys) == 2
    wallets = {row["wallet"] for row in persisted["claims"].values()}
    assert wallets == {W1, W2}

    fresh = AirdropQuest()
    assert len(fresh.claims) == 2
    # B re-synced after its claim.
    assert len(b.claims) == 2


def test_airdrop_same_wallet_two_stale_instances_exactly_one_claim(monkeypatch):
    _verified_registry(monkeypatch)
    _seeded_quest()

    a = AirdropQuest()
    b = AirdropQuest()

    t0 = 1_800_000_000
    monkeypatch.setattr(aqmod, "_now", lambda: t0)
    first = a.claim(W1, "bot-1")
    assert first["ts"] == t0

    # A second (stale) instance claiming the same wallet later must be
    # handed the EXISTING row — no second row, no second credit, and the
    # persisted row keeps the original timestamp.
    monkeypatch.setattr(aqmod, "_now", lambda: t0 + 100)
    second = b.claim(W1, "bot-1")
    assert second["ts"] == t0

    persisted = _persisted_quest(a)
    assert len(persisted["claims"]) == 1
    only = next(iter(persisted["claims"].values()))
    assert only["ts"] == t0
    assert len(AirdropQuest().claims) == 1


def test_airdrop_stale_seed_does_not_erase_claims(monkeypatch):
    _verified_registry(monkeypatch)
    _seeded_quest()

    a = AirdropQuest()
    b = AirdropQuest()  # stale: constructed before A's claim
    a.claim(W1, "bot-1")

    b.seed([W1, W2], source="test")  # re-seed merges, must not drop claims

    persisted = _persisted_quest(a)
    assert len(persisted["claims"]) == 1
    assert len(AirdropQuest().claims) == 1
