"""W-172 regression: MEV routing read view is writable + inclusion
never re-verifies.

Pre-fix (PoC poc50_seal_rep_mevmutate.py):
  * ``LocalPublicMempoolBackend.pending_view()`` returned the LIVE
    queued envelope objects — a mere observer could rewrite a queued
    commit's payload after MAC admission, and ``include_next`` applied
    the mutated (MAC-invalid) commitment to the pipeline.
  * An envelope injected straight into the backend via
    ``backend.submit()`` (never admitted, fake MAC) was applied by
    ``include_next`` with no authentication at all.

Post-fix invariants proven here:
  * ``pending_view()`` hands out deep-copied snapshots; mutating a
    viewed envelope never reaches the pipeline, and mutating the
    caller's envelope after submission never reaches the queue.
  * Inclusion re-verifies every envelope (admission record, MAC,
    freshness, window policy); anything that fails is never applied.
  * The legitimate admit -> include flow still works, including a
    legit envelope queued behind an injected fake.
"""
from __future__ import annotations

import pytest

from sincor2.a2a_inbound import _now_ms, get_fabric, reset_fabric
from sincor2.a2a_inbound_market import create_task
from sincor2.mev_protected_routing import (
    PROTOCOL_VERSION,
    LocalPrivateRoutingBackend,
    LocalPublicMempoolBackend,
    MEVProtectedRouter,
    RoutingEnvelope,
)

SKILL = "cnp9-skill"
H = "agent_h"  # honest bidder
M = "agent_m"  # attacker / sniffer
POSTER = "cnp9-poster"
SECRET = b"test-secret-32-bytes-padded!!"

ORIG = "0x" + "ab" * 32
EVIL = "0x" + "cd" * 32
FAKE = "0x" + "ee" * 32


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
    return MEVProtectedRouter(backend, router_secret=SECRET, **kw)


def _sealed_task():
    task = create_task(skill=SKILL, tags=[SKILL], bounty_axm=1.5,
                       sealed=True, poster_id=POSTER)
    return task["task_id"]


def _stored(fabric, task_id, agent_id):
    rec = fabric.commits.get(f"{task_id}\x00{agent_id}")
    return rec.get("commitment") if rec else None


# ---------------------------------------------------------------------------
# pending_view snapshots (PoC part C)
# ---------------------------------------------------------------------------

def test_pending_view_mutation_does_not_reach_pipeline(fabric_env):
    """PoC part C: a sniffer rewrites the viewed envelope's commitment;
    the pipeline must store the ORIGINAL admitted commitment."""
    fabric = fabric_env
    router = _router(LocalPublicMempoolBackend())
    t1 = _sealed_task()
    router.route_commit(t1, H, ORIG)

    pending = router.backend.pending_view()
    assert len(pending) == 1
    assert pending[0].payload["commitment"] == ORIG
    pending[0].payload["commitment"] = EVIL  # sniffer write via the view

    receipts = router.include_all()
    assert [r.status for r in receipts] == ["included"]
    assert _stored(fabric, t1, H) == ORIG


def test_pending_view_snapshots_are_independent(fabric_env):
    """Mutating one view changes neither a later view nor the queue, and
    mutating the caller's envelope after submission changes nothing."""
    fabric = fabric_env
    router = _router(LocalPublicMempoolBackend())
    t1 = _sealed_task()
    env = router.make_envelope("commit", t1, H, {"commitment": ORIG})
    router.submit_envelope(env)

    view1 = router.backend.pending_view()
    view1[0].payload["commitment"] = EVIL
    env.payload["commitment"] = EVIL  # submitter-side mutation attempt

    view2 = router.backend.pending_view()
    assert view2[0].payload["commitment"] == ORIG
    assert view2[0] is not view1[0]

    router.include_all()
    assert _stored(fabric, t1, H) == ORIG


# ---------------------------------------------------------------------------
# Inclusion re-verification (PoC part C2 + variants)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend", [LocalPublicMempoolBackend(),
                                     LocalPrivateRoutingBackend()])
def test_direct_backend_submit_fake_mac_never_applied(fabric_env, backend):
    """PoC part C2: an envelope injected straight into the backend with
    a fake MAC is never applied — and does not stall the queue: a legit
    envelope admitted afterwards is still included."""
    fabric = fabric_env
    router = _router(backend)
    t1 = _sealed_task()
    fake = RoutingEnvelope(
        protocol_version=PROTOCOL_VERSION, envelope_id="env_fake1",
        task_id=t1, agent_id=M, kind="commit",
        payload={"commitment": FAKE}, issued_at_ms=_now_ms(),
        mac="deadbeef")
    router.backend.submit(fake)  # bypasses router admission entirely

    router.route_commit(t1, H, ORIG)  # legit, admitted, queued behind fake
    receipts = router.include_all()

    assert _stored(fabric, t1, M) is None  # fake never applied
    assert _stored(fabric, t1, H) == ORIG  # legit flow still works
    assert [r.status for r in receipts] == ["included"]


def test_direct_backend_submit_unadmitted_valid_mac_never_applied(fabric_env):
    """A captured/forged envelope with a VALID MAC that this router
    never admitted (injected straight into the backend) is still never
    applied: inclusion requires a live admission record."""
    fabric = fabric_env
    router = _router(LocalPublicMempoolBackend())
    t1 = _sealed_task()
    env = router.make_envelope("commit", t1, H, {"commitment": ORIG})
    assert env.verify_mac(router._secret)
    router.backend.submit(env)  # no submit_envelope -> never admitted

    receipts = router.include_all()
    assert receipts == []
    assert _stored(fabric, t1, H) is None


def test_queued_envelope_mutation_rejected_at_inclusion(fabric_env):
    """Defense in depth: even if a reference to the queued object leaks
    and its payload is rewritten after admission, the inclusion MAC
    re-check rejects it and nothing is applied."""
    fabric = fabric_env
    router = _router(LocalPublicMempoolBackend())
    t1 = _sealed_task()
    router.route_commit(t1, H, ORIG)

    router.backend._pending[0].payload["commitment"] = EVIL  # leaked ref

    receipts = router.include_all()
    assert len(receipts) == 1
    assert receipts[0].status == "failed"
    assert "InvalidEnvelope" in (receipts[0].error or "")
    assert _stored(fabric, t1, H) is None


def test_stale_envelope_rejected_at_inclusion(fabric_env):
    """An envelope admitted fresh but left queued past the freshness
    window is never applied."""
    fabric = fabric_env
    clock = [_now_ms()]
    router = _router(LocalPublicMempoolBackend(),
                     max_envelope_age_ms=60_000,
                     time_fn=lambda: clock[0])
    t1 = _sealed_task()
    router.route_commit(t1, H, ORIG)

    clock[0] += 120_000  # ages out while sitting in the queue
    receipts = router.include_all()
    assert len(receipts) == 1
    assert receipts[0].status == "failed"
    assert "StaleEnvelope" in (receipts[0].error or "")
    assert _stored(fabric, t1, H) is None
