"""A2A governance hardening (2026-11-07) — adversarial tests.

Two confirmed gaps closed here:

1. **Wallet-principal self-bid (KYA identity form mismatch).**
   ``poster_id`` is server-bound at task creation, but it arrives in
   TWO identifier forms: an ``agent_id`` for internal/direct posts and
   the poster's WALLET for external posts (``_server_bound_poster_id``
   returns the recovered EIP-191 signer). The anti-gaming self-bid
   guard compared ``poster_id`` to the bidder's ``agent_id`` as
   strings, so an external poster bidding through its own registered
   agent — same wallet, different string form — was never recognised
   as a self-deal on any bid path. The guard now resolves the bidder's
   KYA wallets (``wallet`` + ``owner_wallet``) and rejects when the
   wallet-form poster matches either.

2. **W-133: sanctions tap envelopes unsigned.**
   ``sanctions_screen._emit_escalation_envelope`` constructed
   ``EscalationStore(sink)`` with no signer, so even with
   ``SINCOR_ESCALATION_SIGNING_KEY_FILE`` configured its audit records
   were written unsigned — the one tap that feeds the governance
   queue bypassed the Phase 2 Ed25519 signing every other store
   honors. The tap now loads the signer from the env-named key file;
   with no key configured the behavior is byte-for-byte the old
   unsigned behavior.

Plus the money-path full-loop integration test (post -> pool allocate
-> commit -> reveal -> settle -> pool spend) on the hardened guard,
proving honest distinct-wallet flow is untouched.
"""
from __future__ import annotations

import os

import pytest

from sincor2 import axm_antigaming as ag
from sincor2.a2a_inbound import _now_ms, get_fabric
from sincor2.a2a_inbound_market import (
    close_auction,
    commit_bid,
    create_task,
    place_bid,
    reveal_bid,
    sealed_commitment,
    submit_proof,
)

W_POSTER = "0x1111111111111111111111111111111111111111"
W_BIDDER = "0x2222222222222222222222222222222222222222"
W_THIRD = "0x3333333333333333333333333333333333333333"

DAY_MS = 24 * 3600 * 1000


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolated fabric + stake ledger + antigaming store + bounty pool."""
    from sincor2.a2a_inbound import reset_fabric
    from sincor2.onchain.stake_ledger import reset_stake_ledger
    from sincor2.a2a_bounty_pool import reset_bounty_pool

    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    store = ag.reset_antigaming_store(path=str(tmp_path / "antigaming.json"))
    monkeypatch.setenv("SINCOR_LAUNCH_BOUNTY_AXM", "100")
    reset_bounty_pool(path=str(tmp_path / "pool.json"))
    monkeypatch.setenv("AXM_AG_MAX_BIDS_PER_HOUR", "1000")
    monkeypatch.setenv("AXM_AG_MAX_WINS_PER_HOUR", "1000")
    monkeypatch.setenv("AXM_AG_MAX_CONSECUTIVE_WINS", "3")
    monkeypatch.setenv("AXM_AG_MAX_PAIR_CONSECUTIVE", "2")
    monkeypatch.setenv("AXM_AG_MAX_PAIR_WINS_PER_WINDOW", "1000")
    return store


def _agent(agent_id, wallet, *, origin="external", age_days=30):
    from sincor2.onchain.stake_ledger import stake_ledger

    fabric = get_fabric()
    fabric.agents[agent_id] = {
        "agent_id": agent_id,
        "capability_tags": ["gig"],
        "last_heartbeat": _now_ms(),
        "reputation": 0.5,
        "wallet": wallet,
        "owner_wallet": wallet,
        "origin": origin,
        "registered_at": _now_ms() - age_days * DAY_MS,
    }
    stake_ledger().deposit(agent_id, 100 * 10**18)


def _commit(task_id, agent_id, bid_axm=0.9):
    salt = os.urandom(32)
    price_wei = int(round(float(bid_axm) * 1e18))
    commitment = "0x" + sealed_commitment(price_wei, salt, agent_id).hex()
    rec = commit_bid(task_id, agent_id, commitment)
    return rec, salt.hex()


# --- 1a. wallet-form poster: self-bid rejected on every bid path -------------

def test_wallet_poster_cannot_commit_own_task(iso):
    _agent("ext-poster", W_POSTER)
    task = create_task("gig", tags=["gig"], bounty_axm=1.0, sealed=True,
                       poster_id=W_POSTER)
    with pytest.raises(ag.SelfBidError):
        _commit(task["task_id"], "ext-poster", 0.9)
    # Rejected BEFORE the stake lock: no commit record exists.
    fabric = get_fabric()
    assert not any(k.startswith(task["task_id"]) for k in fabric.commits)


def test_wallet_poster_cannot_plaintext_bid_own_task(iso):
    _agent("ext-poster", W_POSTER)
    task = create_task("gig", tags=["gig"], bounty_axm=1.0, sealed=False,
                       poster_id=W_POSTER)
    with pytest.raises(ag.SelfBidError):
        place_bid(task["task_id"], "ext-poster", 0.9, 60)


def test_wallet_poster_matches_owner_wallet_field(iso):
    # Registration binds owner_wallet; the record's display wallet may
    # differ. A poster wallet equal to the bidder's OWNER wallet is
    # the same principal and must be rejected.
    _agent("owner-bidder", W_BIDDER)
    get_fabric().agents["owner-bidder"]["wallet"] = W_THIRD
    task = create_task("gig", tags=["gig"], bounty_axm=1.0, sealed=True,
                       poster_id=W_BIDDER)
    with pytest.raises(ag.SelfBidError):
        _commit(task["task_id"], "owner-bidder", 0.9)


def test_wallet_self_bid_excluded_at_winner_selection(iso):
    # A bid planted directly in the fabric (bypassing the commit gate,
    # the pre-guard legacy shape) must not WIN: close_auction
    # re-validates eligibility and excludes it.
    _agent("sneaky-ext", W_POSTER)
    task = create_task("gig", tags=["gig"], bounty_axm=1.0, sealed=False,
                       poster_id=W_POSTER)
    task_id = task["task_id"]
    fabric = get_fabric()
    fabric.bids["bid_planted"] = {
        "bid_id": "bid_planted",
        "task_id": task_id,
        "agent_id": "sneaky-ext",
        "bid_axm": 0.9,
        "score": 999.0,
        "received_at": _now_ms(),
        "revealed": True,
    }
    fabric.tasks[task_id]["auction_closes_at"] = _now_ms() - 1
    snap = close_auction(task_id)
    assert snap["state"] == "expired"
    assert snap.get("assigned_to") is None


def test_distinct_wallet_bidder_on_wallet_poster_task_allowed(iso):
    # Control: an honest bidder whose wallet differs from the poster's
    # wallet bids, reveals, and wins exactly as before.
    _agent("ext-poster", W_POSTER)
    _agent("honest-bidder", W_BIDDER)
    task = create_task("gig", tags=["gig"], bounty_axm=1.0, sealed=True,
                       poster_id=W_POSTER)
    task_id = task["task_id"]
    _, nonce_hex = _commit(task_id, "honest-bidder", 0.9)
    fabric = get_fabric()
    fabric.tasks[task_id]["commit_deadline"] = _now_ms() - 1000
    reveal_bid(task_id, "honest-bidder", 0.9, nonce_hex, 60)
    fabric.tasks[task_id]["reveal_deadline"] = _now_ms() - 1
    snap = close_auction(task_id)
    assert snap["state"] == "assigned"
    assert snap["assigned_to"] == "honest-bidder"


# --- 1b. full loop: post -> allocate -> commit -> reveal -> settle -> spend --

def test_full_loop_pool_backed_settlement_with_wallet_guards(iso):
    from sincor2.a2a_bounty_pool import bounty_pool

    # Pool auto-funds 100 AXM from the configured reserve on boot
    # (SINCOR_LAUNCH_BOUNTY_AXM in the fixture); no manual fund needed.
    assert abs(bounty_pool().status()["funded_axm"] - 100.0) < 1e-9
    # Internal-origin poster is pool-eligible (engine exemption).
    _agent("engine-poster", W_POSTER, origin="internal")
    _agent("worker-1", W_BIDDER)
    task = create_task("gig", tags=["gig"], bounty_axm=1.0, sealed=True,
                       poster_id="engine-poster")
    task_id = task["task_id"]
    assert task["pool_backed"] is True
    allocs = [a for a in bounty_pool().allocations_for_task(task_id)]
    assert len(allocs) == 1 and allocs[0]["state"] == "allocated"

    _, nonce_hex = _commit(task_id, "worker-1", 0.9)
    fabric = get_fabric()
    fabric.tasks[task_id]["commit_deadline"] = _now_ms() - 1000
    reveal_bid(task_id, "worker-1", 0.9, nonce_hex, 60)
    fabric.tasks[task_id]["reveal_deadline"] = _now_ms() - 1
    snap = close_auction(task_id)
    assert snap["state"] == "assigned" and snap["assigned_to"] == "worker-1"

    proof = submit_proof(task_id, "worker-1", "0x" + "ab" * 32)
    assert proof["task_id"] == task_id
    assert get_fabric().tasks[task_id]["state"] == "settled"
    # allocations_for_task() is the LIVE (state == "allocated") view by
    # design (admin delist); a settled task's allocation has left it.
    # The spent state is read from the full status allocation list.
    assert bounty_pool().allocations_for_task(task_id) == []
    status = bounty_pool().status()
    spent = [a for a in status["allocations"]
             if a["task_id"] == task_id and a["state"] == "spent"]
    assert len(spent) == 1
    assert abs(status["spent_axm"] - 1.0) < 1e-9
    assert abs(status["allocated_axm"]) < 1e-9


# --- 2. W-133: sanctions tap honors the escalation signing key ---------------

def _fresh_snapshot():
    import datetime

    from sincor2.defi.ofac_sdn import load_snapshot

    snap = dict(load_snapshot())
    snap["fetched_at"] = datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    return snap


FLAGGED_ADDR = "0x252a8bd2319d8a555b872990601221b3a2053bce"


def test_sanctions_tap_signs_when_signing_key_configured(
        monkeypatch, tmp_path):
    import sincor2.escalation as esc
    import sincor2.sanctions_screen as ss

    key_path = tmp_path / "signing.pem"
    pubkey = esc.generate_signing_key(str(key_path))
    monkeypatch.setenv(esc.SIGNING_KEY_ENV_VAR, str(key_path))
    sink = tmp_path / "sink"
    monkeypatch.setenv(ss.ESCALATION_SINK_ENV, str(sink))

    res = ss.screen_wallet(FLAGGED_ADDR, snapshot=_fresh_snapshot())
    assert res["verdict"] == "flagged"

    store = esc.EscalationStore(str(sink))
    records = store.read_audit_log()
    assert records, "tap emitted no audit records"
    assert all("signature" in r for r in records), (
        "W-133: tap audit records must be Ed25519-signed when "
        "SINCOR_ESCALATION_SIGNING_KEY_FILE is configured")
    assert all(
        r["signature"]["signer_pubkey"] == pubkey for r in records)
    assert store.verify_audit_chain(
        expected_signer_pubkey=pubkey) is True


def test_sanctions_tap_unsigned_without_signing_key(monkeypatch, tmp_path):
    # Control: no key configured -> Phase 1 unsigned behavior preserved.
    import sincor2.escalation as esc
    import sincor2.sanctions_screen as ss

    monkeypatch.delenv(esc.SIGNING_KEY_ENV_VAR, raising=False)
    sink = tmp_path / "sink"
    monkeypatch.setenv(ss.ESCALATION_SINK_ENV, str(sink))

    res = ss.screen_wallet(FLAGGED_ADDR, snapshot=_fresh_snapshot())
    assert res["verdict"] == "flagged"
    store = esc.EscalationStore(str(sink))
    records = store.read_audit_log()
    assert records
    assert all("signature" not in r for r in records)
    assert store.verify_audit_chain() is True
