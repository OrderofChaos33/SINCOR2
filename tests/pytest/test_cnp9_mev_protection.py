"""CNP-9: MEV-protected private auction routing.

Unit tests for the routing abstraction (envelope auth, window policy,
backends) plus a two-auction front-running simulation that DEMONSTRATES
the protection:

  * public-mempool backend: a sniffer reads a PENDING reveal on task T1
    (bid_axm = 10), races an undercutting commit (9.5) into sibling task
    T2, and WINS T2.
  * private-routing-sim backend: the sniffer sees nothing pending, commits
    blind (10.5), and LOSES T2 to the honest bidder (10).

The simulation uses the REAL auction pipeline (commit_bid / reveal_bid /
close_auction) at inclusion time — only the transport's pre-inclusion
observability differs between the two arms.
"""
from __future__ import annotations

import os

import pytest

from sincor2.a2a_inbound import _now_ms, get_fabric, reset_fabric
from sincor2.a2a_inbound_market import create_task, sealed_commitment
from sincor2.mev_protected_routing import (
    InvalidEnvelope,
    LocalPrivateRoutingBackend,
    LocalPublicMempoolBackend,
    MEVProtectedRouter,
    PrivateMempoolBackend,
    PrivateRoutingNotAuthorized,
    ReplayedEnvelope,
    StaleEnvelope,
    ThresholdDecryptionBackend,
    WindowViolation,
    make_backend,
)

SKILL = "cnp9-skill"
H = "agent_h"  # honest bidder
M = "agent_m"  # mempool sniffer / sniper


@pytest.fixture
def fabric_env(tmp_path):
    """Isolated fabric + stake ledger with two funded agents."""
    from sincor2.onchain.stake_ledger import reset_stake_ledger, stake_ledger

    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    fabric = get_fabric()
    now = _now_ms()
    for aid in (H, M):
        fabric.agents[aid] = {
            "agent_id": aid,
            "capability_tags": [SKILL],
            "last_heartbeat": now,
            "reputation": 0.5,
            "wallet": "0x" + ("33" if aid == H else "44") * 20,
        }
        stake_ledger().deposit(aid, 20 * 10**18)
    return fabric


def _router(backend, **kw):
    return MEVProtectedRouter(backend, router_secret=b"test-secret-32-bytes-padded!!", **kw)


def _commitment(bid_axm, nonce, agent_id):
    return "0x" + sealed_commitment(int(round(bid_axm * 1e18)), nonce, agent_id).hex()


def _sealed_task(fabric_env):
    task = create_task(skill=SKILL, tags=[SKILL], bounty_axm=1.5, sealed=True)
    return task["task_id"]


# ---------------------------------------------------------------------------
# Envelope authentication
# ---------------------------------------------------------------------------

def test_envelope_mac_roundtrip():
    router = _router(LocalPrivateRoutingBackend())
    env = router.make_envelope("commit", "t1", H, {"commitment": "0x" + "ab" * 32})
    assert env.mac and env.verify_mac(router._secret)


def test_envelope_tamper_cross_task_replay_rejected(fabric_env):
    """A captured envelope retargeted at another task fails authentication
    (task_id is MAC-bound)."""
    router = _router(LocalPrivateRoutingBackend())
    t1, t2 = _sealed_task(fabric_env), _sealed_task(fabric_env)
    env = router.make_envelope("commit", t1, H, {"commitment": "0x" + "ab" * 32})
    forged = router.make_envelope("commit", t1, H, {"commitment": "0x" + "ab" * 32})
    # Frozen dataclass: rebuild with the attacker's task id instead of mutating.
    retargeted = type(env)(
        protocol_version=env.protocol_version,
        envelope_id=env.envelope_id,
        task_id=t2,  # attacker swaps the task id, MAC no longer matches
        agent_id=env.agent_id,
        kind=env.kind,
        payload=dict(env.payload),
        issued_at_ms=env.issued_at_ms,
        mac=env.mac,
    )
    assert forged.task_id == t1
    with pytest.raises(InvalidEnvelope):
        router.submit_envelope(retargeted)


def test_envelope_replay_rejected(fabric_env):
    router = _router(LocalPrivateRoutingBackend())
    t1 = _sealed_task(fabric_env)
    env = router.make_envelope("commit", t1, H, {"commitment": "0x" + "ab" * 32})
    router.submit_envelope(env)
    with pytest.raises(ReplayedEnvelope):
        router.submit_envelope(env)


def test_envelope_stale_rejected(fabric_env):
    router = _router(LocalPrivateRoutingBackend(), max_envelope_age_ms=60_000)
    t1 = _sealed_task(fabric_env)
    env = router.make_envelope(
        "commit", t1, H, {"commitment": "0x" + "ab" * 32},
        issued_at_ms=_now_ms() - 3600_000)
    with pytest.raises(StaleEnvelope):
        router.submit_envelope(env)


# ---------------------------------------------------------------------------
# Hardened reveal-window policy (admission-time, fail-closed)
# ---------------------------------------------------------------------------

def test_commit_rejected_after_commit_deadline(fabric_env):
    fabric = fabric_env
    router = _router(LocalPrivateRoutingBackend())
    t1 = _sealed_task(fabric)
    fabric.tasks[t1]["commit_deadline"] = _now_ms() - 1000  # window shut
    with pytest.raises(WindowViolation, match="commit window closed"):
        router.route_commit(t1, H, "0x" + "ab" * 32)


def test_reveal_rejected_while_commit_window_open(fabric_env):
    """Hardened: no reveal-while-committing, even with a valid commitment."""
    fabric = fabric_env
    router = _router(LocalPrivateRoutingBackend())
    t1 = _sealed_task(fabric)  # commit window open
    nonce = os.urandom(32)
    router.route_commit(t1, H, _commitment(10.0, nonce, H))
    router.include_all()
    with pytest.raises(WindowViolation, match="reveal window not open yet"):
        router.route_reveal(t1, H, 10.0, nonce.hex())


def test_reveal_rejected_after_reveal_deadline(fabric_env):
    fabric = fabric_env
    router = _router(LocalPrivateRoutingBackend())
    t1 = _sealed_task(fabric)
    nonce = os.urandom(32)
    router.route_commit(t1, H, _commitment(10.0, nonce, H))
    router.include_all()
    now = _now_ms()
    fabric.tasks[t1]["commit_deadline"] = now - 2000
    fabric.tasks[t1]["reveal_deadline"] = now - 1000  # reveal shut too
    with pytest.raises(WindowViolation, match="reveal window closed"):
        router.route_reveal(t1, H, 10.0, nonce.hex())


def test_reveal_without_commit_rejected(fabric_env):
    fabric = fabric_env
    router = _router(LocalPrivateRoutingBackend())
    t1 = _sealed_task(fabric)
    fabric.tasks[t1]["commit_deadline"] = _now_ms() - 1000  # reveal phase
    with pytest.raises(WindowViolation, match="no commitment"):
        router.route_reveal(t1, H, 10.0, os.urandom(32).hex())


# ---------------------------------------------------------------------------
# Backend observability semantics
# ---------------------------------------------------------------------------

def test_public_backend_pending_visible(fabric_env):
    fabric = fabric_env
    router = _router(LocalPublicMempoolBackend())
    t1 = _sealed_task(fabric)
    nonce = os.urandom(32)
    router.route_commit(t1, H, _commitment(10.0, nonce, H))
    router.include_all()
    fabric.tasks[t1]["commit_deadline"] = _now_ms() - 1000
    receipt = router.route_reveal(t1, H, 10.0, nonce.hex())
    pending = router.backend.pending_view()
    assert len(pending) == 1
    assert pending[0].payload["bid_axm"] == 10.0  # sniffer reads the value
    assert receipt.observable_pre_inclusion is True


def test_private_backend_pending_hidden(fabric_env):
    fabric = fabric_env
    router = _router(LocalPrivateRoutingBackend())
    t1 = _sealed_task(fabric)
    nonce = os.urandom(32)
    router.route_commit(t1, H, _commitment(10.0, nonce, H))
    router.include_all()
    fabric.tasks[t1]["commit_deadline"] = _now_ms() - 1000
    receipt = router.route_reveal(t1, H, 10.0, nonce.hex())
    assert router.backend.pending_view() == []  # sniffer sees nothing
    assert router.backend.queued() == 1  # but the envelope is queued
    assert receipt.observable_pre_inclusion is False
    router.include_all()
    key = f"{t1}\x00{H}"
    assert fabric.commits[key]["revealed"] is True  # applied at inclusion


def test_live_backends_blocked():
    """No live private-mempool / threshold-decryption integration without
    founder approval: the factory refuses, and the stubs raise."""
    with pytest.raises(PrivateRoutingNotAuthorized):
        make_backend("private-mempool")
    with pytest.raises(PrivateRoutingNotAuthorized):
        make_backend("threshold-decryption")
    with pytest.raises(PrivateRoutingNotAuthorized):
        PrivateMempoolBackend().submit(None)
    with pytest.raises(PrivateRoutingNotAuthorized):
        ThresholdDecryptionBackend().submit(None)
    with pytest.raises(ValueError):
        make_backend("bogus-backend")


# ---------------------------------------------------------------------------
# Front-running simulation: two sibling auctions, one sniffer
# ---------------------------------------------------------------------------

def _run_sniping_scenario(fabric, backend_name):
    """Returns (winner_of_t2, sniffer_saw_pending_reveal).

    T1 is in reveal phase; T2 is in commit phase. H commits+reveals 10 AXM
    on T1 through the router. M sniffs the backend's pending view, then
    commits on T2: informed undercut (9.5) if it saw H's reveal, blind
    prior (10.5) otherwise. H also bids 10 on T2. T2 is closed via the
    router and the winner is read from the real pipeline state.
    """
    router = _router(make_backend(backend_name))
    now = _now_ms()
    t1 = _sealed_task(fabric)
    t2 = _sealed_task(fabric)

    # H commits 10 AXM on T1 while its commit window is open.
    nonce_h1 = os.urandom(32)
    router.route_commit(t1, H, _commitment(10.0, nonce_h1, H))
    router.include_all()

    # T1 moves to reveal phase; T2 stays in commit phase.
    fabric.tasks[t1]["commit_deadline"] = _now_ms() - 1000
    router.route_reveal(t1, H, 10.0, nonce_h1.hex())

    # M sniffs the pending view BEFORE inclusion.
    pending = router.backend.pending_view()
    saw = any(e.kind == "reveal" and e.task_id == t1 for e in pending)
    if saw:
        m_price = float(pending[0].payload["bid_axm"]) - 0.5  # informed undercut
    else:
        m_price = 10.5  # blind prior

    # Both bid on T2 (commit phase).
    nonce_m2, nonce_h2 = os.urandom(32), os.urandom(32)
    router.route_commit(t2, M, _commitment(m_price, nonce_m2, M))
    router.route_commit(t2, H, _commitment(10.0, nonce_h2, H))
    router.include_all()
    # Apply H's T1 reveal too (it was pending during the sniff).
    router.include_all()

    # T2 moves to reveal phase, then both reveal.
    fabric.tasks[t2]["commit_deadline"] = _now_ms() - 1000
    router.route_reveal(t2, M, m_price, nonce_m2.hex())
    router.route_reveal(t2, H, 10.0, nonce_h2.hex())
    router.include_all()

    # Reveal window over -> permissionless close through the router.
    fabric.tasks[t2]["reveal_deadline"] = _now_ms() - 1000
    router.route_close(t2)
    receipts = router.include_all()
    assert any(r.kind == "close" and r.status == "included" for r in receipts)
    return fabric.tasks[t2]["assigned_to"], saw, m_price


def test_frontrun_public_mempool_sniper_wins(fabric_env):
    """Control arm: on the public path the sniffer reads H's pending reveal
    and undercuts on the sibling auction -> M wins T2."""
    winner, saw, m_price = _run_sniping_scenario(fabric_env, "public")
    assert saw is True
    assert m_price == 9.5
    assert winner == M


def test_frontrun_private_routing_sniper_loses(fabric_env):
    """Protected arm: on the private path the sniffer sees nothing pending,
    commits blind, and loses T2 to the honest bidder."""
    winner, saw, m_price = _run_sniping_scenario(fabric_env, "private-sim")
    assert saw is False
    assert m_price == 10.5
    assert winner == H


# ---------------------------------------------------------------------------
# Close routing, window status, audit trail
# ---------------------------------------------------------------------------

def test_route_close_applies_close(fabric_env):
    fabric = fabric_env
    router = _router(LocalPrivateRoutingBackend())
    t1 = _sealed_task(fabric)
    nonce = os.urandom(32)
    router.route_commit(t1, H, _commitment(10.0, nonce, H))
    router.include_all()
    now = _now_ms()
    fabric.tasks[t1]["commit_deadline"] = now - 2000
    fabric.tasks[t1]["reveal_deadline"] = now - 1000
    router.route_close(t1)
    receipts = router.include_all()
    assert receipts[0].kind == "close"
    assert receipts[0].status == "included"
    assert fabric.tasks[t1]["state"] == "expired"  # no reveals -> timeout


def test_reveal_window_status_phases(fabric_env):
    fabric = fabric_env
    router = _router(LocalPrivateRoutingBackend())
    t1 = _sealed_task(fabric)
    assert router.reveal_window_status(t1)["phase"] == "commit"
    now = _now_ms()
    fabric.tasks[t1]["commit_deadline"] = now - 1000
    assert router.reveal_window_status(t1)["phase"] == "reveal"
    fabric.tasks[t1]["reveal_deadline"] = now - 500
    assert router.reveal_window_status(t1)["phase"] == "closed"
    assert router.reveal_window_status("nope")["phase"] == "unknown"


def test_audit_log_records_observability(fabric_env):
    """Who-saw-what-when: the audit log records pre-inclusion
    observability per envelope."""
    fabric = fabric_env
    router = _router(LocalPublicMempoolBackend())
    t1 = _sealed_task(fabric)
    router.route_commit(t1, H, "0x" + "cd" * 32)
    log = router.audit_log()
    assert len(log) == 1
    assert log[0].kind == "commit"
    assert log[0].observable_pre_inclusion is True
    assert log[0].backend == "public-mempool-sim"
    assert log[0].status == "submitted"
    router.include_all()
    assert router.audit_log()[0].status == "included"
    assert router.audit_log()[0].inclusion_seq == 1
