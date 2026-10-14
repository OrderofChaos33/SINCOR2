"""W-81 distinct-wallet sybil-ring tests (hunt-16 re-grounding, 2026-10-14).

The wallet-cluster layer (``quarantine_check``) is blind to an operator
who mints one fresh EOA per identity — every cluster is size 1. This
suite covers the ring layer in ``sybil_defense``: registration-burst
cohorts correlated with winner standing and POSTER standing.

Rework (reviewer REJECT of the first build): the block keys on the
poster's own standing, not on poster-in-cohort timing — the attacker
shifts the poster's registration 61 minutes early for free. Newborn
burst + newborn poster => BLOCKED. Newborn burst + platform poster =>
HELD for manual review. Newborn burst + established poster => audit
flag only.

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
from sincor2.a2a_inbound_market import (
    _reject_sybil_hold,
    _release_sybil_hold,
    submit_proof,
)
from sincor2.sybil_defense import (
    SYBIL_BURST_WINDOW_MS,
    _burst_cohort,
    _ring_assessment,
    quarantine_check,
    sybil_burst_window_ms,
    sybil_poster_low_reputation,
    sybil_ring_cohort_size,
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
# The bypass, closed: the block keys on poster STANDING, not timing
# ---------------------------------------------------------------------------

def test_ring_burst_distinct_wallets_newborn_poster_blocked():
    """Core W-81: distinct wallets, burst-minted, newborn, paid by a
    newborn poster — the payout is blocked even though every wallet
    cluster is size 1."""
    poster, workers = _burst_ring(3)
    winner = workers[0]

    # The old layer is blind (every wallet a singleton); only the ring
    # layer fires — and without task context it flags but never blocks.
    v0 = quarantine_check(winner)
    assert v0["cluster_size"] == 1
    assert v0["ring"]["ring_shaped"] is True
    assert v0["quarantined"] is False
    assert v0["held"] is False

    _assigned_task_as("task-ring-1", winner, poster)
    with pytest.raises(PermissionError, match="quarantined"):
        submit_proof("task-ring-1", winner, "0x" + "ab" * 16)
    # The block fires before any state mutation.
    assert get_fabric().tasks["task-ring-1"]["state"] == "assigned"

    verdict = quarantine_check(winner, task_id="task-ring-1")
    assert verdict["quarantined"] is True
    assert verdict["ring"]["quarantined"] is True
    assert verdict["ring"]["cohort_size"] == 4  # poster + 3 workers
    assert verdict["ring"]["winner_low_reputation"] is True
    assert verdict["ring"]["poster_low_reputation"] is True
    assert "sybil ring" in verdict["reason"]


def test_ring_poster_registered_61min_early_still_blocked():
    """REGRESSION for the reviewer's live evasion: the poster registers
    61 minutes BEFORE the worker burst (dodging any timing-based
    poster-in-cohort gate for free). The block must still fire because
    the poster's STANDING is newborn, not because of its timing."""
    poster_key = Account.create()
    _register("early-poster", poster_key)
    fabric = get_fabric()
    # Attacker ages only the poster record, not its standing.
    fabric.agents["early-poster"]["registered_at"] = (
        _now_ms() - (SYBIL_BURST_WINDOW_MS + 60_000))
    workers = []
    for i in range(3):
        key = Account.create()
        aid = f"late-w{i}"
        _register(aid, key)
        workers.append(aid)
    winner = workers[0]

    # Sanity: the poster is NOT in the winner's burst cohort (the old
    # gate would have missed this entirely).
    assert "early-poster" not in _burst_cohort(winner)

    _assigned_task_as("task-ring-evasion", winner, "early-poster")
    verdict = quarantine_check(winner, task_id="task-ring-evasion")
    assert verdict["quarantined"] is True, (
        "poster-timing evasion must not dodge the standing-based block")
    assert verdict["ring"]["poster_low_reputation"] is True
    with pytest.raises(PermissionError, match="quarantined"):
        submit_proof("task-ring-evasion", winner, "0x" + "ab" * 16)


def test_ring_established_poster_flag_only_not_blocked():
    """A newborn burst paid by an ESTABLISHED poster (rep >= 1.0) is
    legitimate shape — paid, with an audit-visible ring flag that is
    telemetry, not a control."""
    poster, workers = _burst_ring(3)
    get_fabric().agents[poster]["reputation"] = 2.0
    winner = workers[0]

    _assigned_task_as("task-ring-est", winner, poster)
    proof = submit_proof("task-ring-est", winner, "0x" + "cd" * 16)
    assert proof["status"] == "paid"
    sybil = proof["payout"].get("sybil") or {}
    assert sybil.get("flagged") is True
    ring = sybil.get("ring") or {}
    assert ring["cohort_size"] == 4
    assert ring["poster_low_reputation"] is False
    assert ring["note"] == "flag only — not a control"


def test_ring_reputation_escape_hatch():
    """A burst identity that earned standing (>= 1.0 == five settlements)
    is not blocked even when its poster is newborn: an established
    winner did real work."""
    poster, workers = _burst_ring(3)
    winner = workers[0]
    get_fabric().agents[winner]["reputation"] = 5.0

    _assigned_task_as("task-ring-3", winner, poster)
    proof = submit_proof("task-ring-3", winner, "0x" + "ef" * 16)
    assert proof["status"] == "paid"
    verdict = quarantine_check(winner, task_id="task-ring-3")
    assert verdict["quarantined"] is False
    assert verdict["held"] is False
    # Established winner: no ring signal at all (not even a flag).
    assert verdict["ring"]["ring_shaped"] is False


# ---------------------------------------------------------------------------
# The hold: platform-posted and poster-unknown tasks
# ---------------------------------------------------------------------------

def test_ring_internal_poster_held_not_paid():
    """Newborn burst winner on a platform-posted (internal origin) task:
    the payout is HELD for manual review — not staged, not settled."""
    _register("platform-poster", Account.create())
    get_fabric().agents["platform-poster"]["origin"] = "internal"
    workers = []
    for i in range(3):
        key = Account.create()
        aid = f"plat-w{i}"
        _register(aid, key)
        workers.append(aid)
    winner = workers[0]

    _assigned_task_as("task-ring-hold", winner, "platform-poster")
    verdict = quarantine_check(winner, task_id="task-ring-hold")
    assert verdict["quarantined"] is False
    assert verdict["held"] is True
    assert verdict["ring"]["poster_origin"] == "internal"

    proof = submit_proof("task-ring-hold", winner, "0x" + "ab" * 16)
    assert proof["status"] == "held"
    assert proof["payout"] is None  # nothing staged
    task = get_fabric().tasks["task-ring-hold"]
    assert task["state"] == "held"
    assert task["sybil_hold"]["agent_id"] == winner
    assert task["sybil_hold"]["amount_axm"] == 1.0
    # No reputation granted, no settlement fields.
    assert "payout_axm" not in task
    assert get_fabric().agents[winner].get("reputation") in (0, 0.0, None)


def test_ring_unknown_poster_held_fail_closed():
    """Task whose poster has no agent record: standing cannot be
    assessed, so the payout is held (fail closed), not paid."""
    _burst_ring(3)
    winner = "ring-w0"

    _assigned_task_as("task-ring-unk", winner, "ghost-poster")  # not registered
    verdict = quarantine_check(winner, task_id="task-ring-unk")
    assert verdict["held"] is True
    assert verdict["quarantined"] is False
    proof = submit_proof("task-ring-unk", winner, "0x" + "cd" * 16)
    assert proof["status"] == "held"
    assert get_fabric().tasks["task-ring-unk"]["state"] == "held"


def test_ring_hold_release_settles_and_pays():
    """Admin release: the held payout stages, settles, grants rep."""
    _register("platform-poster", Account.create())
    get_fabric().agents["platform-poster"]["origin"] = "internal"
    for i in range(3):
        _register(f"rel-w{i}", Account.create())
    winner = "rel-w0"

    _assigned_task_as("task-ring-rel", winner, "platform-poster")
    proof = submit_proof("task-ring-rel", winner, "0x" + "ab" * 16)
    assert proof["status"] == "held"

    released = _release_sybil_hold("task-ring-rel", decided_by="test-admin")
    assert released["status"] == "paid"
    assert released["payout"]["sybil"]["held"] is True
    assert released["payout"]["sybil"]["released_by"] == "test-admin"
    task = get_fabric().tasks["task-ring-rel"]
    assert task["state"] == "settled"
    assert task["payout_axm"] == 1.0
    assert "sybil_hold" not in task
    assert task["sybil_hold_decision"]["decision"] == "released"
    # Reputation granted exactly once.
    assert get_fabric().agents[winner]["reputation"] == 0.2


def test_ring_hold_reject_moves_no_money():
    """Admin reject: no payout, no rep, task marked rejected, proof kept."""
    _register("platform-poster", Account.create())
    get_fabric().agents["platform-poster"]["origin"] = "internal"
    for i in range(3):
        _register(f"rej-w{i}", Account.create())
    winner = "rej-w0"

    _assigned_task_as("task-ring-rej", winner, "platform-poster")
    proof = submit_proof("task-ring-rej", winner, "0x" + "ab" * 16)
    assert proof["status"] == "held"

    rejected = _reject_sybil_hold("task-ring-rej", decided_by="test-admin")
    assert rejected["status"] == "rejected"
    task = get_fabric().tasks["task-ring-rej"]
    assert task["state"] == "rejected"
    assert "payout_axm" not in task
    assert task["sybil_hold_decision"]["decision"] == "rejected"
    assert get_fabric().agents[winner].get("reputation") in (0, 0.0, None)


def test_ring_hold_release_unknown_task_404_shape():
    """Releasing a non-held task is a KeyError (the HTTP layer maps it
    to 404)."""
    with pytest.raises(KeyError, match="no held payout"):
        _release_sybil_hold("task-nope")
    with pytest.raises(KeyError, match="no held payout"):
        _reject_sybil_hold("task-nope")


# ---------------------------------------------------------------------------
# Honest use stays clean
# ---------------------------------------------------------------------------

def test_ring_legit_multiwallet_under_cohort_size_clean():
    """Two identities, distinct wallets, established poster: no burst
    cohort of 3, no flag, no block."""
    ka, kb = Account.create(), Account.create()
    _register("honest-a", ka)
    _register("honest-b", kb)
    _register("honest-poster", Account.create())
    fabric = get_fabric()
    fabric.agents["honest-poster"]["reputation"] = 3.0
    # The poster registered long before the pair: not part of the burst.
    fabric.agents["honest-poster"]["registered_at"] = (
        _now_ms() - 2 * SYBIL_BURST_WINDOW_MS)

    assert _burst_cohort("honest-a") == ["honest-a", "honest-b"]
    _assigned_task_as("task-ring-4", "honest-a", "honest-poster")
    proof = submit_proof("task-ring-4", "honest-a", "0x" + "12" * 16)
    assert proof["status"] == "paid"
    assert "sybil" not in proof["payout"]


def test_ring_staggered_registrations_break_the_burst():
    """Three identities on distinct wallets but registered hours apart:
    no burst cohort, so even a newborn poster does not trip the ring."""
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


def test_ring_no_task_context_flags_but_never_blocks_or_holds():
    """Without task context the ring layer can flag (burst + newborn)
    but cannot establish poster standing, so it never blocks or holds —
    there is no payout to gate."""
    _burst_ring(3)
    verdict = quarantine_check("ring-w0")
    assert verdict["ring"]["ring_shaped"] is True
    assert verdict["ring"]["poster_id"] is None
    assert verdict["flagged"] is True
    assert verdict["quarantined"] is False
    assert verdict["held"] is False


# ---------------------------------------------------------------------------
# Env-tunable policy
# ---------------------------------------------------------------------------

def test_ring_thresholds_env_tunable(monkeypatch):
    """Policy is not baked into code: cohort size, window, and both
    reputation thresholds honor env overrides."""
    assert sybil_ring_cohort_size() == 3
    assert sybil_burst_window_ms() == SYBIL_BURST_WINDOW_MS
    assert sybil_poster_low_reputation() == 1.0

    monkeypatch.setenv("SYBIL_RING_COHORT_SIZE", "5")
    monkeypatch.setenv("SYBIL_BURST_WINDOW_MS", "60000")
    monkeypatch.setenv("SYBIL_POSTER_LOW_REPUTATION", "0.5")
    assert sybil_ring_cohort_size() == 5
    assert sybil_burst_window_ms() == 60_000
    assert sybil_poster_low_reputation() == 0.5

    # A 4-identity burst no longer trips the (raised) cohort gate.
    poster, workers = _burst_ring(3)  # 4 with poster
    verdict = quarantine_check(workers[0], task_id="task-x")
    assert verdict["ring"]["cohort_size"] == 4
    assert verdict["ring"]["ring_shaped"] is False
    assert verdict["quarantined"] is False

    # Garbage env falls back to defaults (fail safe, not fail weird).
    monkeypatch.setenv("SYBIL_RING_COHORT_SIZE", "not-a-number")
    assert sybil_ring_cohort_size() == 3


def test_ring_poster_threshold_blocks_only_below_it(monkeypatch):
    """With SYBIL_POSTER_LOW_REPUTATION=0.5, a poster at rep 0.4 is
    newborn (blocked) while a poster at 0.6 is established (flag only)."""
    monkeypatch.setenv("SYBIL_POSTER_LOW_REPUTATION", "0.5")
    poster, workers = _burst_ring(3)
    winner = workers[0]

    get_fabric().agents[poster]["reputation"] = 0.4
    _assigned_task_as("task-ring-pt1", winner, poster)
    assert quarantine_check(winner, task_id="task-ring-pt1")["quarantined"] is True

    get_fabric().agents[poster]["reputation"] = 0.6
    _assigned_task_as("task-ring-pt2", winner, poster)
    verdict = quarantine_check(winner, task_id="task-ring-pt2")
    assert verdict["quarantined"] is False
    assert verdict["ring"]["ring_shaped"] is True  # audit flag remains


# ---------------------------------------------------------------------------
# Adversarial self-review: honest residuals
# ---------------------------------------------------------------------------

def test_ring_residual_slow_roll_registration_evades_burst():
    """DOCUMENTED RESIDUAL: one identity per window-plus-epsilon dodges
    the burst tripwire. Cost scales LINEARLY with fleet size (N x ~61
    min wall-clock; ~4 days per 100 identities) — not O(1). The
    identities remain visible to registration-velocity metrics; this
    test pins the boundary so a future tightening is deliberate, not
    accidental."""
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
        "tripwire at linear wall-clock cost; mitigation is "
        "registration-velocity monitoring, not this gate")


def test_ring_residual_poster_farming_costs_real_work():
    """DOCUMENTED RESIDUAL: a poster that earned 1.0 via five genuine
    settlements can fund newborn bursts (flag-only). The 1.0 line costs
    five real tasks with bound deliverables — doing real work is the
    desired outcome, not an evasion. This test pins that the flag still
    fires (audit-visible) even when the block does not."""
    poster, workers = _burst_ring(3)
    get_fabric().agents[poster]["reputation"] = 1.0  # exactly at threshold
    winner = workers[0]

    _assigned_task_as("task-ring-farm", winner, poster)
    verdict = quarantine_check(winner, task_id="task-ring-farm")
    # rep < threshold is strict: 1.0 is established, not newborn.
    assert verdict["ring"]["poster_low_reputation"] is False
    assert verdict["quarantined"] is False
    assert verdict["ring"]["ring_shaped"] is True
    proof = submit_proof("task-ring-farm", winner, "0x" + "ab" * 16)
    assert proof["status"] == "paid"
    assert (proof["payout"].get("sybil") or {}).get("ring") is not None
