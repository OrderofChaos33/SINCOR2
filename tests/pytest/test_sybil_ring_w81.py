"""W-81 distinct-wallet sybil-ring tests (hunt-16 re-grounding, 2026-10-14).

The wallet-cluster layer (``quarantine_check``) is blind to an operator
who mints one fresh EOA per identity — every cluster is size 1. This
suite covers the ring layer in ``sybil_defense``: registration-burst
cohorts correlated with newborn standing and poster linkage.

No Flask server, no chain. KYA + fabric persistence are redirected to
temp dirs and both registries are reset per test (mirrors
test_sybil_fix.py).
"""
from __future__ import annotations

import os
import shutil
import tempfile

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from sincor2 import kya_registry as kya
from sincor2.a2a_inbound import (
    _now_ms,
    get_fabric,
    reset_fabric,
)
from sincor2.a2a_identity import register_message as _new_registration_message
from sincor2.a2a_inbound_ext import register_agent_record
from sincor2.a2a_inbound_market import submit_proof
from sincor2.sybil_defense import (
    SYBIL_BURST_WINDOW_MS,
    quarantine_check,
    _burst_cohort,
    _ring_assessment,
)


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    """Hermetic KYA + fabric state per test (mirrors test_sybil_fix.py)."""
    kya_dir = tempfile.mkdtemp(prefix="sybil_ring_kya_")
    store_fd, store_path = tempfile.mkstemp(prefix="sybil_ring_kya_", suffix=".json")
    os.close(store_fd)
    if os.path.exists(store_path):
        os.unlink(store_path)
    monkeypatch.setenv("KYA_STORE_PATH", store_path)
    monkeypatch.setenv("SINCOR_DATA_DIR", kya_dir)
    kya.reset()
    reset_fabric()
    yield
    kya.reset()
    reset_fabric()
    if os.path.exists(store_path):
        os.unlink(store_path)
    shutil.rmtree(kya_dir, ignore_errors=True)


def _body(agent_id: str, wallet: str, **extra):
    body = {
        "agent_id": agent_id,
        "name": agent_id,
        "description": "ring probe agent",
        "version": "0.1.0",
        "capability_tags": ["lead-enrichment"],
        "skills": [{"id": "lead-enrichment", "name": "Lead Enrichment"}],
        "rpc_callback": "https://probe.example/rpc",
        "wallet": wallet,
        "chain_id": 8453,
    }
    body.update(extra)
    return body


def _register(agent_id: str, key, **extra):
    """Register with a VALID EIP-191 wallet proof (attacker controls keys)."""
    body = _body(agent_id, key.address, **extra)
    ts = _now_ms()
    message = _new_registration_message(agent_id, ts)
    sig = key.sign_message(encode_defunct(text=message)).signature
    body["registration_wallet"] = key.address
    body["registration_ts"] = ts
    body["registration_signature"] = "0x" + bytes(sig).hex()
    return register_agent_record(body)


def _assigned_task_as(task_id: str, agent_id: str, poster_id: str,
                      assigned_ago_ms: int = 1_000):
    fabric = get_fabric()
    fabric.tasks[task_id] = {
        "task_id": task_id,
        "state": "assigned",
        "assigned_to": agent_id,
        "assigned_at": _now_ms() - assigned_ago_ms,
        "time_est_ms": 60_000,
        "tags": ["lead-enrichment"],
        "poster_id": poster_id,
        "winning_bid_axm": 1.0,
        "bounty_axm": 1.0,
    }
    return task_id


def _burst_ring(n_workers: int = 3):
    """Mint a poster + n_workers, each on a distinct wallet, in one burst."""
    poster_key = Account.create()
    _register("ring-poster", poster_key)
    workers = []
    for i in range(n_workers):
        key = Account.create()
        aid = f"ring-w{i}"
        _register(aid, key)
        workers.append(aid)
    return "ring-poster", workers


# ---------------------------------------------------------------------------
# The bypass, closed
# ---------------------------------------------------------------------------

def test_ring_burst_distinct_wallets_newborn_poster_in_cohort_blocked():
    """Core W-81: distinct wallets, burst-minted, newborn, paid by a
    cohort poster — the payout is blocked even though every wallet
    cluster is size 1."""
    poster, workers = _burst_ring(3)
    winner = workers[0]

    # The old layer is blind (every wallet a singleton); only the ring
    # layer fires — and without task context it flags but never blocks.
    v0 = quarantine_check(winner)
    assert v0["cluster_size"] == 1
    assert v0["ring"]["ring_shaped"] is True
    assert v0["quarantined"] is False

    _assigned_task_as("task-ring-1", winner, poster)
    with pytest.raises(PermissionError, match="quarantined"):
        submit_proof("task-ring-1", winner, "0x" + "ab" * 16)
    # The block fires before any state mutation.
    assert get_fabric().tasks["task-ring-1"]["state"] == "assigned"

    verdict = quarantine_check(winner, task_id="task-ring-1")
    assert verdict["quarantined"] is True
    assert verdict["ring"]["quarantined"] is True
    assert verdict["ring"]["cohort_size"] == 4  # poster + 3 workers
    assert verdict["ring"]["poster_in_cohort"] is True
    assert "sybil ring" in verdict["reason"]


def test_ring_flagged_not_blocked_when_poster_is_external():
    """Burst newborns paid by an OUTSIDE poster: suspicious enough to
    flag on the receipt, not conclusive enough to block."""
    _burst_ring(3)
    winner = "ring-w0"

    _assigned_task_as("task-ring-2", winner, "poster-1")  # not registered
    proof = submit_proof("task-ring-2", winner, "0x" + "cd" * 16)
    assert proof["status"] == "paid"
    sybil = proof["payout"].get("sybil") or {}
    assert sybil.get("flagged") is True
    ring = sybil.get("ring") or {}
    assert ring["cohort_size"] == 4
    assert ring["poster_in_cohort"] is False


def test_ring_reputation_escape_hatch():
    """A burst identity that earned standing (>= 1.0 == five settlements)
    is not blocked even when its poster is in the cohort."""
    poster, workers = _burst_ring(3)
    winner = workers[0]
    get_fabric().agents[winner]["reputation"] = 5.0

    _assigned_task_as("task-ring-3", winner, poster)
    proof = submit_proof("task-ring-3", winner, "0x" + "ef" * 16)
    assert proof["status"] == "paid"
    verdict = quarantine_check(winner, task_id="task-ring-3")
    assert verdict["quarantined"] is False
    # Still ring-shaped (poster in cohort) → flagged, not blocked.
    assert verdict["ring"]["ring_shaped"] is True


# ---------------------------------------------------------------------------
# Honest use stays clean
# ---------------------------------------------------------------------------

def test_ring_legit_multiwallet_under_cohort_size_clean():
    """Two identities, distinct wallets, open-market poster: no burst
    cohort of 3, no flag, no block."""
    ka, kb = Account.create(), Account.create()
    _register("honest-a", ka)
    _register("honest-b", kb)

    assert _burst_cohort("honest-a") == ["honest-a", "honest-b"]
    _assigned_task_as("task-ring-4", "honest-a", "poster-1")
    proof = submit_proof("task-ring-4", "honest-a", "0x" + "12" * 16)
    assert proof["status"] == "paid"
    assert "sybil" not in proof["payout"]


def test_ring_staggered_registrations_break_the_burst():
    """Three identities on distinct wallets but registered hours apart:
    no burst cohort, so even an in-group poster does not trip the ring."""
    keys = [Account.create() for _ in range(3)]
    aids = ["stagger-0", "stagger-1", "stagger-2"]
    for aid, key in zip(aids, keys):
        _register(aid, key)
    fabric = get_fabric()
    now = _now_ms()
    for i, aid in enumerate(aids):
        fabric.agents[aid]["registered_at"] = now - i * (SYBIL_BURST_WINDOW_MS + 60_000)

    assert _burst_cohort("stagger-0") == ["stagger-0"]
    _assigned_task_as("task-ring-5", "stagger-0", "stagger-1")
    proof = submit_proof("task-ring-5", "stagger-0", "0x" + "34" * 16)
    assert proof["status"] == "paid"
    assert "sybil" not in proof["payout"]


def test_ring_internal_agents_excluded_from_cohort():
    """Platform-internal agents minted in the same burst do not inflate
    (or join) the cohort."""
    ka, kb = Account.create(), Account.create()
    _register("ext-0", ka)
    _register("ext-1", kb)
    ki1, ki2 = Account.create(), Account.create()
    _register("int-0", ki1)
    _register("int-1", ki2)
    fabric = get_fabric()
    fabric.agents["int-0"]["origin"] = "internal"
    fabric.agents["int-1"]["origin"] = "internal"

    assert _burst_cohort("ext-0") == ["ext-0", "ext-1"]
    assert _burst_cohort("int-0") == []
    assert _ring_assessment("ext-0")["cohort_size"] == 2


def test_ring_no_task_context_flags_but_never_blocks():
    """Without task context the ring layer can flag (burst + newborn)
    but cannot establish poster linkage, so it never blocks."""
    _burst_ring(3)
    verdict = quarantine_check("ring-w0")
    assert verdict["ring"]["ring_shaped"] is True
    assert verdict["ring"]["poster_in_cohort"] is False
    assert verdict["flagged"] is True
    assert verdict["quarantined"] is False


# ---------------------------------------------------------------------------
# Adversarial self-review: the documented residual
# ---------------------------------------------------------------------------

def test_ring_residual_slow_roll_registration_evades_burst():
    """DOCUMENTED RESIDUAL: one identity per window-plus-epsilon dodges
    the burst tripwire — at ~60x the time cost of a burst mint. The
    identities remain visible to registration-velocity metrics and the
    W-89 wash-anomaly guard; this test pins the boundary so a future
    tightening is deliberate, not accidental."""
    keys = [Account.create() for _ in range(3)]
    aids = ["slow-0", "slow-1", "slow-2"]
    for aid, key in zip(aids, keys):
        _register(aid, key)
    fabric = get_fabric()
    now = _now_ms()
    for i, aid in enumerate(aids):
        fabric.agents[aid]["registered_at"] = now - i * (SYBIL_BURST_WINDOW_MS + 1)

    _assigned_task_as("task-ring-6", "slow-0", "slow-1")
    verdict = quarantine_check("slow-0", task_id="task-ring-6")
    assert verdict["ring"]["cohort_size"] == 1
    assert verdict["quarantined"] is False, (
        "documented residual: slow-roll registration evades the burst "
        "tripwire; mitigation is registration-velocity monitoring, "
        "not this gate")
