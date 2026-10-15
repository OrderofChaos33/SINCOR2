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
    assert proof["status"] == "payout_staged"
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
    assert proof["status"] == "payout_staged"
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
    assert released["status"] == "payout_staged"
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
    assert proof["status"] == "payout_staged"
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
    assert proof["status"] == "payout_staged"
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
    """DOCUMENTED RESIDUAL (corrected round 3): a poster at marketplace
    rep 1.0 funds newborn bursts flag-only on the ring layer. The old
    docstring claimed the 1.0 line cost "five genuine settlements" —
    the reviewer EXECUTED five dust-bounty self-deals to 1.0 at ~zero
    cost, so the display scalar is manufacturable and gates nothing of
    value. What gates the PROFIT is the pool-backing gate
    (``poster_pool_eligible``): the dust-farmed poster scores R_i ~0
    (tenure-gated) and its tasks post self-funded, so the self-deal
    loop loses the 5% fee per cycle. This test pins that the ring flag
    still fires (audit-visible) even when the block does not; see
    ``test_pool_gate_dust_farmed_poster_denied`` for the profit gate.
    """
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
    assert proof["status"] == "payout_staged"
    assert (proof["payout"].get("sybil") or {}).get("ring") is not None


# ---------------------------------------------------------------------------
# W-81 round 3: pool-backing gate, hold race, env fail-closed, poster-ID
# squatting, hold TTL
# ---------------------------------------------------------------------------

import threading as _threading

from sincor2.a2a_bounty_pool import bounty_pool, reset_bounty_pool
from sincor2.activity_engine import POSTER_ID, ensure_engine_poster
from sincor2.a2a_inbound_ext import PROTECTED_AGENT_IDS
from sincor2.onchain.stake_ledger import reset_stake_ledger, stake_ledger
from sincor2.a2a_inbound_market import (
    _expire_sybil_hold_if_due,
    create_task,
)
from sincor2.sybil_defense import (
    SYBIL_POOL_POSTER_REPUTATION,
    poster_pool_eligible,
    sybil_hold_ttl_ms,
    sybil_pool_poster_reputation,
)


@pytest.fixture
def _pool_ledger_isolation(tmp_path):
    """Funded pool + fresh stake ledger; singletons restored after."""
    import sincor2.a2a_bounty_pool as pool_mod
    import sincor2.onchain.stake_ledger as ledger_mod
    old_pool, old_ledger = pool_mod._POOL, ledger_mod._LEDGER
    pool_mod._POOL = None
    ledger_mod._LEDGER = None
    reset_bounty_pool(path=str(tmp_path / "pool.json"), reserve_axm=20000)
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    yield
    pool_mod._POOL, ledger_mod._LEDGER = old_pool, old_ledger


def _hold_task_for(winner_prefix: str, poster_id: str, task_id: str,
                   held_ago_ms: int = 1_000):
    """Drive a newborn-burst winner on an internal-poster task into a hold."""
    _register("hold-poster", Account.create())
    get_fabric().agents["hold-poster"]["origin"] = "internal"
    for i in range(3):
        _register(f"hold-w{i}", Account.create())
    _assigned_task_as(task_id, "hold-w0", "hold-poster")
    proof = submit_proof(task_id, "hold-w0", "0x" + "ab" * 16)
    assert proof["status"] == "held"
    if held_ago_ms != 1_000:
        get_fabric().tasks[task_id]["sybil_hold"]["held_at"] = (
            _now_ms() - held_ago_ms)
    return task_id


# ---------------------------------------------------------------------------
# Finding 1: pool backing gated on poster standing (R_i, not the
# dust-farmable display scalar)
# ---------------------------------------------------------------------------

def test_pool_gate_dust_farmed_poster_denied(_pool_ledger_isolation):
    """REGRESSION (W-81 finding 1, reviewer-executed attack): dust-farm a
    poster to marketplace rep 1.0 via five ~zero-cost self-deals, post a
    large pool-backed bounty, win it with a newborn burst. The pool must
    NOT allocate: the gate prices standing in ledger R_i (tenure-gated,
    ~0 for the farmed identity), not the manufacturable display scalar.
    The task posts self-funded; the self-deal loop then loses the 5%
    platform fee per cycle — unprofitable by construction.
    """
    poster_key = Account.create()
    _register("dust-poster", poster_key)
    fabric = get_fabric()
    # The reviewer's executed farming, simulated at the state level: the
    # display scalar reaches 1.0 while the ledger records five
    # manufactured settlements today (tenure ~0 -> R_i ~0).
    fabric.agents["dust-poster"]["reputation"] = 1.0
    ledger = stake_ledger()
    for i in range(5):
        ledger.record_completion("dust-poster", f"dust-task-{i}",
                                 poster_id="dust-accomplice")
    assert ledger.reputation_score("dust-poster") < sybil_pool_poster_reputation()

    pool = bounty_pool()
    avail_before = pool.status()["available_axm"]
    task = create_task("lead-enrichment", bounty_axm=100.0,
                       poster_id="dust-poster")
    assert task["pool_backed"] is False
    assert "pool_backing_skipped_reason" in task
    # No pool funds moved: the extraction is zero, hence unprofitable
    # (the operator funded the 100 AXM bounty themselves and the 5%
    # platform fee burns on every self-deal cycle).
    assert pool.status()["available_axm"] == avail_before
    assert pool.status()["allocated_axm"] == 0
    assert pool.allocations_for_task(task["task_id"]) == []
    # The gate never bricks task creation: the task posts self-funded.
    assert task["state"] == "open"
    assert task["poster_id"] == "dust-poster"


def test_pool_gate_engine_internal_poster_allowed(_pool_ledger_isolation):
    """The engine's own tasks keep flowing: internal origin is exempt
    from the standing gate (origin is server-stamped, not spoofable)."""
    assert ensure_engine_poster() is True
    rec = get_fabric().agents[POSTER_ID]
    assert rec["origin"] == "internal"

    task = create_task("lead-enrichment", bounty_axm=2.0, sealed=True,
                       poster_id=POSTER_ID)
    assert task["pool_backed"] is True
    assert task["pool_allocation_id"]
    pool = bounty_pool()
    assert pool.allocations_for_task(task["task_id"])


def test_pool_gate_established_poster_allowed(_pool_ledger_isolation):
    """A poster with real tenure-gated standing (R_i >= threshold)
    unlocks pool backing."""
    _register("veteran-poster", Account.create())
    ledger = stake_ledger()
    # 50 completions across 3 posters, first seen 90 days ago -> R_i = 1.0.
    now_s = int(__import__("time").time())
    rep = ledger._rep("veteran-poster")
    for i in range(50):
        rep["completions"].append({
            "task_id": f"vet-task-{i}",
            "poster": f"vet-client-{i % 3}",
            "ts": now_s - 45 * 86400,
        })
    rep["first_seen"] = now_s - 90 * 86400
    ledger._save()
    assert ledger.reputation_score("veteran-poster") >= sybil_pool_poster_reputation()

    task = create_task("lead-enrichment", bounty_axm=2.0,
                       poster_id="veteran-poster")
    assert task["pool_backed"] is True


def test_pool_gate_anonymous_and_unknown_poster_self_funded(
        _pool_ledger_isolation):
    """Anonymous posts and unattributable posters post self-funded —
    fail-closed on the subsidy, never on creation."""
    pool = bounty_pool()
    avail_before = pool.status()["available_axm"]

    t1 = create_task("lead-enrichment", bounty_axm=1.0, poster_id=None)
    assert t1["pool_backed"] is False

    t2 = create_task("lead-enrichment", bounty_axm=1.0,
                     poster_id="nobody-here")
    assert t2["pool_backed"] is False

    assert pool.status()["available_axm"] == avail_before


def test_pool_gate_wallet_poster_resolves(_pool_ledger_isolation):
    """External server-bound posts attribute by signer WALLET: the gate
    resolves the wallet to the agent record (and its R_i)."""
    key = Account.create()
    _register("wallet-poster", key)
    ledger = stake_ledger()
    now_s = int(__import__("time").time())
    rep = ledger._rep("wallet-poster")
    for i in range(50):
        rep["completions"].append({
            "task_id": f"wvet-task-{i}",
            "poster": f"wvet-client-{i % 3}",
            "ts": now_s - 45 * 86400,
        })
    rep["first_seen"] = now_s - 90 * 86400
    ledger._save()

    eligible, reason = poster_pool_eligible(key.address.lower())
    assert eligible is True, reason
    task = create_task("lead-enrichment", bounty_axm=2.0,
                       poster_id=key.address.lower())
    assert task["pool_backed"] is True


# ---------------------------------------------------------------------------
# Finding 2: double-release race settles exactly once
# ---------------------------------------------------------------------------

def test_hold_double_release_settles_exactly_once():
    """REGRESSION (W-81 finding 2): two concurrent admin releases of the
    same hold settle exactly once — the loser returns the already-recorded
    outcome (race_noop) instead of double-settling."""
    task_id = _hold_task_for("hold-w0", "hold-poster", "task-race-1")
    fabric = get_fabric()
    winner = "hold-w0"

    # Widen the race window: stall inside stage_payout so both threads
    # pass the first lock before either reaches the second.
    import sincor2.a2a_inbound_market as mkt
    real_stage = mkt.stage_payout

    def slow_stage(**kw):
        _threading.Event().wait(0.3)
        return real_stage(**kw)

    mkt.stage_payout = slow_stage
    results = []
    try:
        def _release():
            try:
                results.append(_release_sybil_hold(task_id, decided_by="t"))
            except Exception as err:  # pragma: no cover - diagnostic
                results.append(err)
        t1 = _threading.Thread(target=_release)
        t2 = _threading.Thread(target=_release)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
    finally:
        mkt.stage_payout = real_stage

    assert len(results) == 2
    assert not any(isinstance(r, Exception) for r in results), results
    # The loser returns the winner's already-recorded "payout_staged" proof plus
    # a race_noop annotation: classify by the annotation, not the status.
    noop = [r for r in results
            if (r.get("sybil_hold") or {}).get("race_noop")]
    paid = [r for r in results
            if r.get("status") == "payout_staged" and not (
                (r.get("sybil_hold") or {}).get("race_noop"))]
    assert len(paid) == 1, results
    assert len(noop) == 1, results
    # Exactly one settlement: +0.2 rep once, task settled once.
    assert fabric.agents[winner]["reputation"] == 0.2
    assert fabric.tasks[task_id]["state"] == "settled"
    assert "sybil_hold" not in fabric.tasks[task_id]


def test_hold_release_vs_reject_race_single_decision():
    """A release racing a reject yields exactly one decision — no
    double-settle, no lost update."""
    task_id = _hold_task_for("hold-w0", "hold-poster", "task-race-2")

    import sincor2.a2a_inbound_market as mkt
    real_stage = mkt.stage_payout

    def slow_stage(**kw):
        _threading.Event().wait(0.3)
        return real_stage(**kw)

    mkt.stage_payout = slow_stage
    results = []
    try:
        def _release():
            try:
                results.append(("release", _release_sybil_hold(task_id)))
            except Exception as err:
                results.append(("release", err))
        def _reject():
            try:
                results.append(("reject", mkt._reject_sybil_hold(task_id)))
            except Exception as err:
                results.append(("reject", err))
        t1 = _threading.Thread(target=_release)
        t2 = _threading.Thread(target=_reject)
        t1.start()
        # Let the release pass its first lock, then reject wins the race.
        _threading.Event().wait(0.1)
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
    finally:
        mkt.stage_payout = real_stage

    # Exactly one terminal decision on the task; the loser either raised
    # KeyError (reject after a completed release) or returned the
    # winner's already-recorded outcome (release after a reject).
    task = get_fabric().tasks[task_id]
    assert task["state"] in ("settled", "rejected")
    assert "sybil_hold" not in task
    decisions = [r[1].get("status") for r in results
                 if not isinstance(r[1], Exception)]
    if task["state"] == "settled":
        assert decisions.count("payout_staged") == 1, decisions
        assert get_fabric().agents["hold-w0"]["reputation"] == 0.2
    else:
        assert "payout_staged" not in decisions, decisions
        assert get_fabric().agents["hold-w0"].get("reputation") in (0, 0.0, None)


# ---------------------------------------------------------------------------
# Finding 3: env fail-open -> fail-closed
# ---------------------------------------------------------------------------

def test_env_negative_and_zero_fall_back_to_defaults(monkeypatch):
    """REGRESSION (W-81 finding 3): SYBIL_BURST_WINDOW_MS=-1 used to
    clamp to a 1ms window (ring neutered, fail-open); negatives/zeros
    now fall back to defaults."""
    from sincor2.sybil_defense import (
        SYBIL_BURST_WINDOW_MS as DEF_WINDOW,
        SYBIL_RING_COHORT_SIZE as DEF_COHORT,
        SYBIL_HOLD_TTL_MS as DEF_TTL,
        SYBIL_LOW_REPUTATION as DEF_REP,
        sybil_burst_window_ms,
        sybil_ring_cohort_size,
        sybil_hold_ttl_ms,
        sybil_low_reputation,
        sybil_pool_poster_reputation,
    )
    monkeypatch.setenv("SYBIL_BURST_WINDOW_MS", "-1")
    assert sybil_burst_window_ms() == DEF_WINDOW
    monkeypatch.setenv("SYBIL_BURST_WINDOW_MS", "0")
    assert sybil_burst_window_ms() == DEF_WINDOW
    monkeypatch.setenv("SYBIL_RING_COHORT_SIZE", "-50")
    assert sybil_ring_cohort_size() == DEF_COHORT
    monkeypatch.setenv("SYBIL_RING_COHORT_SIZE", "0")
    assert sybil_ring_cohort_size() == DEF_COHORT
    monkeypatch.setenv("SYBIL_HOLD_TTL_MS", "-5")
    assert sybil_hold_ttl_ms() == DEF_TTL
    monkeypatch.setenv("SYBIL_LOW_REPUTATION", "-0.5")
    assert sybil_low_reputation() == DEF_REP
    monkeypatch.setenv("SYBIL_LOW_REPUTATION", "0")
    assert sybil_low_reputation() == DEF_REP
    monkeypatch.setenv("SYBIL_POOL_POSTER_REPUTATION", "-1")
    assert sybil_pool_poster_reputation() == SYBIL_POOL_POSTER_REPUTATION
    # Valid values still pass through.
    monkeypatch.setenv("SYBIL_BURST_WINDOW_MS", "60000")
    assert sybil_burst_window_ms() == 60_000


# ---------------------------------------------------------------------------
# Finding 4: engine poster-ID squatting refused
# ---------------------------------------------------------------------------

def test_engine_poster_id_squatting_refused():
    """REGRESSION (W-81 finding 4): external registration of the
    engine's reserved poster ID is refused."""
    assert POSTER_ID in PROTECTED_AGENT_IDS
    body = _body(POSTER_ID, Account.create().address)
    with pytest.raises(ValueError, match="reserved agent_id"):
        register_agent_record(body)


def test_ensure_engine_poster_seeds_internal_and_reclaims():
    """The engine poster seeds as internal; a pre-existing squatter
    record is reclaimed."""
    fabric = get_fabric()
    # Simulate a squat from before the reservation.
    fabric.agents[POSTER_ID] = {
        "agent_id": POSTER_ID, "origin": "external", "reputation": 0.0,
        "wallet": "", "registered_at": _now_ms(),
    }
    assert ensure_engine_poster() is True
    rec = fabric.agents[POSTER_ID]
    assert rec["origin"] == "internal"
    # Idempotent: second call is a no-op.
    assert ensure_engine_poster() is True
    assert get_fabric().agents[POSTER_ID]["origin"] == "internal"


# ---------------------------------------------------------------------------
# Improvement: hold TTL — no capital hostage forever
# ---------------------------------------------------------------------------

def test_hold_ttl_expired_auto_rejects_on_release():
    """A hold past SYBIL_HOLD_TTL_MS auto-rejects (fail closed) instead
    of releasing — money never moves without a human."""
    task_id = _hold_task_for(
        "hold-w0", "hold-poster", "task-ttl-1",
        held_ago_ms=sybil_hold_ttl_ms() + 60_000)
    fabric = get_fabric()

    snap = _release_sybil_hold(task_id, decided_by="test-admin")
    assert snap["status"] == "rejected"
    task = fabric.tasks[task_id]
    assert task["state"] == "rejected"
    assert "payout_axm" not in task
    assert task["sybil_hold_decision"]["decision"] == "rejected"
    assert task["sybil_hold_decision"]["ttl_expired"] is True
    # No reputation granted, no money moved.
    assert fabric.agents["hold-w0"].get("reputation") in (0, 0.0, None)


def test_hold_ttl_not_yet_expired_releases_normally():
    """A hold inside the TTL releases normally."""
    task_id = _hold_task_for("hold-w0", "hold-poster", "task-ttl-2")
    snap = _release_sybil_hold(task_id, decided_by="test-admin")
    assert snap["status"] == "payout_staged"
    assert get_fabric().tasks[task_id]["state"] == "settled"


def test_hold_carries_expires_at():
    """Operators can see the review SLA on the hold record."""
    task_id = _hold_task_for("hold-w0", "hold-poster", "task-ttl-3")
    hold = get_fabric().tasks[task_id]["sybil_hold"]
    assert hold["expires_at"] == hold["held_at"] + sybil_hold_ttl_ms()


def test_expire_sweep_returns_none_when_fresh():
    """The TTL sweep is a no-op for fresh holds and unknown tasks."""
    task_id = _hold_task_for("hold-w0", "hold-poster", "task-ttl-4")
    assert _expire_sybil_hold_if_due(task_id) is None
    assert _expire_sybil_hold_if_due("task-nope") is None
    # Fresh hold untouched.
    assert get_fabric().tasks[task_id]["state"] == "held"
