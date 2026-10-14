"""AXM Activity Engine — continuous gasless (ledger-only) auction loop.

Runs as the central-scheduler job ``activity_engine_cycle``. Every cycle:

1. Ensures the bidder cohort is registered, heartbeated, and staked
   (ledger-only stake; no on-chain deposits).
2. Advances every in-flight auction through
   post -> commit window -> reveal window -> close -> settle.
3. Posts fresh sealed auctions round-robin from the 1000-task DeFi catalog
   (``liveness/defi_catalog``), so many auctions pipeline concurrently.

Gasless means: no private keys, no broadcasts, no fund movement. Payouts
are staged through the existing ``stage_payout`` path (mode="staged") and
the 5% platform fee is recorded as off-chain treasury-inflow accounting
via the existing ``record_platform_fee_inflow`` rail with
``source="a2a_settlement"`` so the activity metrics pick it up.

Crash safety
------------
* Durable cursor (``next_index`` / ``generation``) in a JSON state file
  (atomic tmp+os.replace writes). A restart resumes the same catalog
  position; a task_key is started at most once per generation, so the
  catalog is never repeated until exhausted.
* Posting uses ``create_task(seed_key=...)``: the platform dedupes on the
  seed key, so a crash between post and cursor-advance re-adopts the live
  task instead of double-posting.
* ``commit_bid`` is idempotent per (task, agent); reveal/close/settle are
  all driven from fabric truth each cycle, never from cached flags, so a
  retried step is a no-op.
* A restart inside the reveal window loses the in-memory commit records
  (platform design: ``fabric.commits`` is not persisted). Such auctions
  expire with zero reveals — no ghost slash is possible because there are
  no commit records left to slash — and the engine releases the orphaned
  stake locks and moves on.

Anti-gaming posture
-------------------
* The poster (``axm-activity-engine``) never bids its own tasks.
* Each bidder commits at most once per auction; bids are deterministic
  per (task_key, agent_id) with a real spread (70-95% of bounty), so the
  winner varies per auction.
* Every committing agent always reveals (no ghosting by design).
* Metrics are labeled gasless/ledger-only; nothing is presented as
  on-chain volume.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sincor2.activity_engine")

POSTER_ID = "axm-activity-engine"
ENGINE_NAME = "axm-activity-engine"

# Preferred cohort: the liveness agents, when they exist in the registry.
LIVENESS_COHORT = [f"sincor-liveness-{i:02d}" for i in range(1, 7)]
# Fallback cohort: engine-operated market-maker agents (registered via the
# internal platform-seed path, origin stamped "internal").
ENGINE_COHORT_PREFIX = "axm-engine-bidder"

# Matches A2A_PLATFORM_FEE_BPS (a2a_integration.py). Kept local so this
# module does not import the heavy integration layer.
PLATFORM_FEE_BPS = 500

# Bid spread: each agent bids 70-95% of the bounty (deterministic per
# task_key+agent_id, so restarts reproduce the same plan).
_BID_FRAC_LO = 0.70
_BID_FRAC_HI = 0.95


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def max_in_flight() -> int:
    return _env_int("SINCOR_ACTIVITY_MAX_IN_FLIGHT", 25)


def max_new_per_cycle() -> int:
    return _env_int("SINCOR_ACTIVITY_MAX_NEW_PER_CYCLE", 2)


def bidder_count() -> int:
    return _env_int("SINCOR_ACTIVITY_BIDDER_COUNT", 6)


def catalog_dir() -> Path:
    override = os.environ.get("SINCOR_ACTIVITY_CATALOG_DIR", "").strip()
    if override:
        return Path(override)
    return _project_root() / "liveness" / "defi_catalog"


def state_path() -> Path:
    override = os.environ.get("SINCOR_ACTIVITY_ENGINE_STATE", "").strip()
    if override:
        return Path(override)
    try:
        from sincor2.data_paths import data_dir
        base = data_dir()
    except Exception:
        base = _project_root() / "data"
    return base / "activity_engine" / "state.json"


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

_CATALOG: Optional[List[Dict[str, Any]]] = None


def load_catalog() -> List[Dict[str, Any]]:
    """Load the 1000-task DeFi catalog (deterministic order)."""
    global _CATALOG
    if _CATALOG is not None:
        return _CATALOG
    cdir = catalog_dir()
    files = sorted(cdir.glob("p*.json"))
    if not files:
        raise RuntimeError(f"activity engine catalog empty: {cdir}")
    tasks: List[Dict[str, Any]] = []
    for path in files:
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except Exception as err:
            raise RuntimeError(f"bad catalog file {path}: {err}")
        if not isinstance(items, list):
            raise RuntimeError(f"catalog file {path} is not a task list")
        for t in items:
            if not isinstance(t, dict):
                raise RuntimeError(f"bad catalog entry in {path}")
            for field in ("task_key", "skill", "bounty_axm"):
                if not t.get(field):
                    raise RuntimeError(
                        f"catalog entry missing {field} in {path}")
            tasks.append({
                "task_key": str(t["task_key"]),
                "skill": str(t["skill"]),
                "tags": [str(x).lower() for x in (t.get("tags") or [])],
                "bounty_axm": float(t["bounty_axm"]),
                "title": str(t.get("title") or t["task_key"]),
                "description": str(t.get("description") or ""),
            })
    keys = [t["task_key"] for t in tasks]
    if len(set(keys)) != len(keys):
        raise RuntimeError("catalog task_key values are not unique")
    _CATALOG = tasks
    logger.info("[ACTIVITY] catalog loaded: %d tasks from %s",
                len(tasks), cdir)
    return tasks


def reset_catalog_cache() -> None:
    global _CATALOG
    _CATALOG = None


def catalog_tags() -> List[str]:
    """Union of every tag in the catalog (bidder capability coverage)."""
    tags = set()
    for t in load_catalog():
        tags.update(t["tags"])
    return sorted(tags)


# ---------------------------------------------------------------------------
# Durable state (cursor + in-flight auctions)
# ---------------------------------------------------------------------------

def _default_state() -> Dict[str, Any]:
    return {
        "next_index": 0,
        "generation": 0,
        "cycles": 0,
        "settled_count": 0,
        "expired_count": 0,
        "fees_axm": 0.0,
        "in_flight": {},  # task_key -> record
    }


def load_state() -> Dict[str, Any]:
    path = state_path()
    if not path.is_file():
        return _default_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as err:
        logger.warning("[ACTIVITY] state unreadable (%s); starting fresh", err)
        return _default_state()
    state = _default_state()
    if isinstance(raw, dict):
        for key in state:
            if key in raw:
                state[key] = raw[key]
    if not isinstance(state["in_flight"], dict):
        state["in_flight"] = {}
    return state


def save_state(state: Dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True),
                   encoding="utf-8")
    # Validate before swapping in (JSON state-file edit rule).
    json.loads(tmp.read_text(encoding="utf-8"))
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Cohort (bidders)
# ---------------------------------------------------------------------------

def resolve_cohort() -> List[str]:
    """Bidder agent ids for this cycle.

    Uses the liveness agents where present in the registry; otherwise the
    engine's own market-maker ids (registered on first use).
    ``SINCOR_ACTIVITY_COHORT_IDS`` overrides with a comma list.
    """
    override = os.environ.get("SINCOR_ACTIVITY_COHORT_IDS", "").strip()
    if override:
        # Defense-in-depth: the engine poster must never appear in the bidder
        # cohort, even via operator override (self-bid guard is the backstop).
        return [a.strip() for a in override.split(",")
                if a.strip() and a.strip() != POSTER_ID]
    from sincor2.a2a_inbound import get_fabric
    fabric = get_fabric()
    present = [a for a in LIVENESS_COHORT if a in fabric.agents]
    if present:
        return present
    return [f"{ENGINE_COHORT_PREFIX}-{i:02d}"
            for i in range(1, bidder_count() + 1)]


def _is_engine_agent(agent_id: str) -> bool:
    return agent_id.startswith(ENGINE_COHORT_PREFIX + "-")


def ensure_engine_poster() -> bool:
    """Seed the engine's poster identity as internal (W-81 finding 4).

    POSTER_ID ("axm-activity-engine") is protocol-reserved (see
    ``PROTECTED_AGENT_IDS``): external registration is refused, so the
    first external claim can no longer squat the engine's poster
    identity and poison ring assessments on engine tasks (a squatter's
    newborn record turned HOLDs into QUARANTINEs for honest newcomers).
    The record is seeded here via the internal platform-seed path
    (origin "internal", server-stamped — external origin-spoof attempts
    stay external). If a pre-existing non-internal record holds the id
    (squatted before the reservation), it is reclaimed with a warning.
    Idempotent; safe to call every cycle.
    """
    from sincor2.a2a_inbound import get_fabric, _save_agents
    from sincor2.a2a_inbound_ext import register_agent_record
    fabric = get_fabric()
    existing = fabric.agents.get(POSTER_ID)
    if existing is not None and str(existing.get("origin") or "") == "internal":
        return True
    if existing is not None:
        logger.warning(
            "[ACTIVITY] reclaiming reserved poster id %s from non-internal record",
            POSTER_ID)
    register_agent_record({
        "agent_id": POSTER_ID,
        "name": "AXM Activity Engine",
        "description": (
            "Platform-operated task poster for the gasless AXM activity "
            "loop. Ledger-only; no on-chain activity."),
        "capability_tags": ["defi"],
    }, _internal_reputation=0.0)
    # Honest origin stamp: the engine poster is platform-internal, not
    # external demand (mirrors ensure_cohort_agents).
    with fabric.lock:
        live = fabric.agents.get(POSTER_ID)
        if live is not None:
            live["origin"] = "internal"
    _save_agents(fabric)
    logger.info("[ACTIVITY] seeded engine poster %s (internal)", POSTER_ID)
    return True


def ensure_cohort_agents(agent_ids: List[str]) -> List[str]:
    """Register (if engine-owned) and heartbeat every cohort agent.

    Returns the ids usable this cycle. Registration failures degrade
    gracefully: the cycle continues with whoever is alive.
    """
    from sincor2.a2a_inbound import get_fabric
    from sincor2.a2a_inbound_ext import heartbeat_agent, register_agent_record
    fabric = get_fabric()
    usable: List[str] = []
    for agent_id in agent_ids:
        try:
            if agent_id not in fabric.agents:
                if not _is_engine_agent(agent_id):
                    # Not ours to create (e.g. a liveness id that vanished
                    # from the registry — re-registration is founder-gated).
                    logger.warning(
                        "[ACTIVITY] cohort agent %s not registered; skipping",
                        agent_id)
                    continue
                register_agent_record({
                    "agent_id": agent_id,
                    "name": f"AXM Activity market-maker {agent_id}",
                    "description": (
                        "Engine-operated sealed-bid market maker for the "
                        "gasless AXM activity loop. Ledger-only; no "
                        "on-chain activity."),
                    "capability_tags": catalog_tags() or ["defi"],
                }, _internal_reputation=0.0)
                # Honest origin stamp: engine-operated automation is
                # platform-internal, not external demand.
                with fabric.lock:
                    live = fabric.agents.get(agent_id)
                    if live is not None:
                        live["origin"] = "internal"
                from sincor2.a2a_inbound import _save_agents
                _save_agents(fabric)
                logger.info("[ACTIVITY] registered cohort agent %s",
                            agent_id)
            heartbeat_agent(agent_id)
            usable.append(agent_id)
        except Exception as err:
            logger.warning("[ACTIVITY] cohort agent %s unavailable: %s",
                           agent_id, err)
    return usable


def ensure_cohort_stake(agent_ids: List[str]) -> None:
    """Top up each cohort agent's ledger stake to cover max concurrency.

    Ledger-only accounting (no on-chain movement). Uses the sponsored-stake
    front when the mechanism is enabled, else a plain ledger deposit with an
    honest non-tx reference label.
    """
    from sincor2.onchain.stake_ledger import stake_ledger
    catalog = load_catalog()
    max_bounty = max((t["bounty_axm"] for t in catalog), default=1.0)
    # Worst case: an agent commits on every in-flight auction at once.
    # Commit locks 50% of bounty (minStakeBps=5000) at zero reputation.
    target_wei = int(max_in_flight() * max_bounty * 0.5 * 1e18 * 1.2)
    override = _env_float("SINCOR_ACTIVITY_STAKE_AXM", 0.0)
    if override > 0:
        target_wei = int(override * 1e18)
    try:
        from sincor2.sponsored_stake import (
            sponsored_stake_enabled, front_sponsored_stake,
            sponsored_ledger,
        )
        sponsored = sponsored_stake_enabled()
    except Exception:
        sponsored = False
    ledger = stake_ledger()
    for agent_id in agent_ids:
        try:
            bal = ledger.balance_of(agent_id)
            # Target TOTAL deposited, not available: topping up on available
            # would ratchet the deposit upward every cycle while locks are
            # legitimately held by in-flight auctions.
            deposited = int(bal["deposited_wei"])
        except Exception as err:
            logger.warning("[ACTIVITY] stake balance check failed for %s: %s",
                           agent_id, err)
            continue
        if deposited >= target_wei:
            continue
        top_up = target_wei - deposited
        try:
            if sponsored:
                try:
                    front_sponsored_stake(
                        agent_id, top_up, approved_by="axm-activity-engine")
                except RuntimeError:
                    # Already has an active sponsorship: fall through to a
                    # plain top-up so the agent stays funded.
                    raise _TopUpFallback()
            else:
                raise _TopUpFallback()
        except _TopUpFallback:
            ledger.deposit(
                agent_id, top_up,
                reference="activity-engine:market-maker-funding")
        logger.info("[ACTIVITY] funded %s stake +%d wei (ledger-only)",
                    agent_id, top_up)


class _TopUpFallback(Exception):
    """Internal: use a plain ledger deposit for this top-up."""


# ---------------------------------------------------------------------------
# Bidding plan (deterministic per task_key + agent_id)
# ---------------------------------------------------------------------------

def bid_plan(task_key: str, agent_id: str, bounty_axm: float) -> Dict[str, Any]:
    """Deterministic bid for one agent on one task.

    Seeded PRNG over (task_key, agent_id): bid 70-95% of bounty, staggered
    time estimates. Restarts reproduce the identical plan, so a crash
    between commit and reveal loses nothing.
    """
    seed = hashlib.sha256(
        f"activity-engine|bid|{task_key}|{agent_id}".encode()).digest()
    rng = random.Random(int.from_bytes(seed, "big"))
    frac = _BID_FRAC_LO + (_BID_FRAC_HI - _BID_FRAC_LO) * rng.random()
    bid_axm = round(bounty_axm * frac, 6)
    nonce = hashlib.sha256(
        f"activity-engine|nonce|{task_key}|{agent_id}".encode()).digest()
    time_est_sec = 300 + (int.from_bytes(seed[:2], "big") % 600)
    return {
        "bid_axm": bid_axm,
        "nonce": nonce.hex(),
        "time_est_sec": time_est_sec,
    }


def _commitment_for(plan: Dict[str, Any], agent_id: str) -> str:
    from sincor2.a2a_inbound_market import sealed_commitment
    price_wei = int(round(plan["bid_axm"] * 1e18))
    salt = bytes.fromhex(plan["nonce"])
    return "0x" + sealed_commitment(price_wei, salt, agent_id).hex()


# ---------------------------------------------------------------------------
# Auction lifecycle
# ---------------------------------------------------------------------------

def _seed_key(task_key: str) -> str:
    return f"activity-engine:{task_key}"


def _now_ms() -> int:
    return int(time.time() * 1000)


def post_task(catalog_task: Dict[str, Any],
              state: Dict[str, Any]) -> Dict[str, Any]:
    """Post the next catalog task as a sealed auction (idempotent).

    The ``seed_key`` dedupes inside ``create_task``: a crash between post
    and cursor-advance re-adopts the live task instead of double-posting.
    """
    from sincor2.a2a_inbound_market import create_task
    task_key = catalog_task["task_key"]
    seed = _seed_key(task_key)
    snap = create_task(
        skill=catalog_task["skill"],
        tags=catalog_task["tags"] or [catalog_task["skill"]],
        bounty_axm=catalog_task["bounty_axm"],
        sealed=True,
        poster_id=POSTER_ID,
        seed_key=seed,
        title=catalog_task["title"][:200],
        description=catalog_task["description"][:4000],
    )
    rec = {
        "task_key": task_key,
        "tsk_id": snap["task_id"],
        "seed_key": seed,
        "posted_at": _now_ms(),
        "bounty_axm": float(catalog_task["bounty_axm"]),
        "catalog": {
            "skill": catalog_task["skill"],
            "tags": catalog_task["tags"],
            "title": catalog_task["title"],
            "description": catalog_task["description"],
        },
        "bids": {},          # agent_id -> bid plan
        "winner": None,
        "fee_recorded": False,
        "fee_axm": 0.0,
        "dedupe_hit": bool(snap.get("_dedupe_hit")),
    }
    state["in_flight"][task_key] = rec
    # Advance the durable cursor only after the post is recorded.
    state["next_index"] = (state["next_index"] + 1) % len(load_catalog())
    if state["next_index"] == 0:
        state["generation"] += 1
    logger.info("[ACTIVITY] posted %s as %s (dedupe=%s)",
                task_key, snap["task_id"], rec["dedupe_hit"])
    return rec


def _find_task(rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from sincor2.a2a_inbound import get_fabric
    fabric = get_fabric()
    task = fabric.tasks.get(rec["tsk_id"])
    if task is not None:
        return task
    seed = rec.get("seed_key")
    if seed:
        for t in fabric.tasks.values():
            if t.get("seed_key") == seed and t.get("state") in (
                    "open", "auction", "assigned"):
                return t
    return None


def _drop_in_flight(state: Dict[str, Any], task_key: str,
                    cohort: List[str], reason: str) -> None:
    """Remove a finished auction; release any orphaned stake locks."""
    rec = state["in_flight"].pop(task_key, None)
    if rec is None:
        return
    tsk_id = rec.get("tsk_id")
    if tsk_id and cohort:
        try:
            from sincor2.onchain.stake_ledger import stake_ledger
            ledger = stake_ledger()
            for agent_id in cohort:
                try:
                    ledger.release(agent_id, tsk_id)
                except Exception:
                    pass
        except Exception as err:
            logger.warning("[ACTIVITY] stake release sweep failed: %s", err)
    if reason == "settled":
        state["settled_count"] += 1
    else:
        state["expired_count"] += 1
    logger.info("[ACTIVITY] auction %s %s", task_key, reason)


def _ensure_committed(task: Dict[str, Any], rec: Dict[str, Any],
                      agent_id: str) -> str:
    """Commit this agent's bid if the window is open and not yet committed."""
    from sincor2.a2a_inbound import get_fabric
    from sincor2.a2a_inbound_market import _commit_key, commit_bid
    fabric = get_fabric()
    task_id = task["task_id"]
    if f"{task_id}\x00{agent_id}" in fabric.commits:
        return "already"
    plan = rec["bids"].get(agent_id)
    if plan is None:
        plan = bid_plan(rec["task_key"], agent_id, rec["bounty_axm"])
        rec["bids"][agent_id] = plan
    try:
        commit_bid(task_id, agent_id, _commitment_for(plan, agent_id))
    except PermissionError as err:
        # Not eligible this cycle (merit gate, heartbeat, capability).
        # Retry next cycle — reputation grows as the cohort settles.
        logger.debug("[ACTIVITY] %s cannot commit on %s: %s",
                     agent_id, task_id, err)
        return f"skipped: {err}"
    except (KeyError, RuntimeError, ValueError) as err:
        logger.warning("[ACTIVITY] commit failed %s on %s: %s",
                       agent_id, task_id, err)
        return f"error: {err}"
    return "committed"


def _ensure_revealed(task: Dict[str, Any], rec: Dict[str, Any],
                     agent_id: str) -> str:
    from sincor2.a2a_inbound import get_fabric
    from sincor2.a2a_inbound_market import reveal_bid
    fabric = get_fabric()
    task_id = task["task_id"]
    commit = fabric.commits.get(f"{task_id}\x00{agent_id}")
    if commit is None:
        return "no-commit"  # agent skipped commit; nothing to reveal
    if commit.get("revealed"):
        return "already"
    plan = rec["bids"].get(agent_id)
    if plan is None:
        # Restart inside the reveal window: the plan is deterministic, so
        # rebuild it. (The fabric commit itself is gone in this case, so
        # close_auction will find zero commits and expire the auction.)
        plan = bid_plan(rec["task_key"], agent_id, rec["bounty_axm"])
        rec["bids"][agent_id] = plan
    try:
        reveal_bid(task_id, agent_id, plan["bid_axm"],
                   "0x" + plan["nonce"], plan["time_est_sec"])
    except (PermissionError, RuntimeError, ValueError, KeyError) as err:
        logger.warning("[ACTIVITY] reveal failed %s on %s: %s",
                       agent_id, task_id, err)
        return f"error: {err}"
    return "revealed"


def _settle(task: Dict[str, Any], rec: Dict[str, Any]) -> str:
    """Winner submits proof; payout stages gasless; task settles."""
    from sincor2.a2a_inbound_market import submit_proof
    task_id = task["task_id"]
    winner = task.get("assigned_to")
    if not winner:
        return "no-winner"
    receipt_preimage = (
        f"activity-engine|settlement|{rec['task_key']}|{winner}"
        f"|{_now_ms()}")
    receipt_hash = "0x" + hashlib.sha256(
        receipt_preimage.encode()).hexdigest()
    # Bare-receipt path (documented legacy form): the engine has no
    # deliverable artifact to bind — the settlement IS the ledger entry —
    # and inventing a deliverable_uri would fabricate provenance. The
    # settlement detail lives in the engine's own state record instead.
    try:
        proof = submit_proof(task_id, winner, receipt_hash)
    except Exception as err:
        logger.warning("[ACTIVITY] settle failed %s: %s", task_id, err)
        return f"error: {err}"
    rec["winner"] = winner
    rec["payout_axm"] = proof.get("payout", {}).get("amount_axm")
    rec["settled_tsk"] = task_id
    rec["settlement"] = {
        "mode": "gasless-ledger-only",
        "receipt_hash": receipt_hash,
        "content_bound": False,
        "note": "No on-chain activity; payout staged, fee recorded as "
                "off-chain accounting.",
    }
    logger.info("[ACTIVITY] settled %s winner=%s payout=%s",
                rec["task_key"], winner, rec["payout_axm"])
    return "settled"


def _record_fee(task: Dict[str, Any], rec: Dict[str, Any],
                state: Dict[str, Any]) -> Optional[float]:
    """Record the 5% treasury fee (off-chain accounting, idempotent)."""
    if rec.get("fee_recorded"):
        return rec.get("fee_axm")
    amount = float(task.get("payout_axm")
                   or task.get("winning_bid_axm")
                   or rec.get("bounty_axm") or 0)
    fee = round(amount * PLATFORM_FEE_BPS / 10000, 6)
    if fee <= 0:
        rec["fee_recorded"] = True
        return 0.0
    from sincor2.contract_net import payout_broadcast_status
    from sincor2.treasury_settlement import record_platform_fee_inflow
    # Honesty gate (not key absence): the fee ledger only ever carries a
    # shape-validated broadcast hash. If a future staged-path regression
    # stores a digest (or anything else) under the task's payout_tx key,
    # it cannot silently flow into treasury accounting as a real tx hash.
    payout = payout_broadcast_status({"ok": True, "tx_hash": task.get("payout_tx")})
    tx_hash = payout["tx_hash"] if payout["broadcasted"] else None
    # W-89 wash-anomaly quarantine: a flagged settlement is never booked as
    # realized revenue.  The fee is recorded projected (audit-visible) so a
    # wash cannot manufacture realized treasury inflow.
    anomaly = (task.get("wash_anomaly") or {})
    quarantined = bool(anomaly.get("anomalous"))
    if quarantined:
        logger.error("[WASH-GUARD] fee quarantine: task %s flagged %s; "
                     "booking fee as PROJECTED, not realized",
                     task.get("task_id"),
                     [s.get("name") for s in anomaly.get("signals", [])])
    recorded = record_platform_fee_inflow(
        fee_amount=fee,
        asset="AXM",
        source="a2a_settlement",
        tx_hash=tx_hash or None,
        task_id=task.get("task_id"),
        note=f"platform fee activity-engine task={rec['task_key']}",
        projected=quarantined,
    )
    if recorded is None:
        logger.warning("[ACTIVITY] fee record failed for %s",
                       rec["task_key"])
        return None
    rec["fee_recorded"] = True
    rec["fee_axm"] = fee
    rec["fee_projected"] = quarantined
    state["fees_axm"] = round(state.get("fees_axm", 0.0) + fee, 6)
    logger.info("[ACTIVITY] fee %.6f AXM recorded for %s%s",
                fee, rec["task_key"],
                " (PROJECTED — wash-anomaly)" if quarantined else "")
    return fee


def advance_auction(rec: Dict[str, Any], cohort: List[str],
                    state: Dict[str, Any]) -> str:
    """Advance one in-flight auction one step. Returns an outcome label."""
    from sincor2.a2a_inbound_market import close_auction
    task = _find_task(rec)
    if task is None:
        # Lost (e.g. memory-store restart wiped the fabric): re-post the
        # same catalog task under the same seed key.
        catalog_task = dict(rec["catalog"])
        catalog_task["task_key"] = rec["task_key"]
        catalog_task["bounty_axm"] = rec["bounty_axm"]
        post_task(catalog_task, state)
        # post_task advanced the cursor; pull it back so the re-post does
        # not consume the next catalog slot.
        state["next_index"] = (state["next_index"] - 1) % len(load_catalog())
        if state["next_index"] == len(load_catalog()) - 1:
            state["generation"] = max(0, state["generation"] - 1)
        return "reposted"
    # Keep the record's tsk_id current (seed-key re-adoption may rotate it).
    rec["tsk_id"] = task["task_id"]
    task_state = task.get("state")
    if task_state in ("settled", "failed"):
        if task_state == "settled":
            _record_fee(task, rec, state)
        _drop_in_flight(state, rec["task_key"], cohort, task_state)
        return task_state
    if task_state == "expired":
        _drop_in_flight(state, rec["task_key"], cohort, "expired")
        return "expired"
    if task_state == "assigned":
        winner = task.get("assigned_to")
        if winner in cohort:
            return _settle(task, rec)
        # External winner: they submit their own proof; fee is recorded
        # when the task settles.
        return "awaiting-external-proof"
    if task_state in ("open", "auction"):
        now = _now_ms()
        commit_dl = task.get("commit_deadline")
        reveal_dl = task.get("reveal_deadline")
        if commit_dl is not None and now < int(commit_dl):
            results = [_ensure_committed(task, rec, a) for a in cohort]
            return "committing:" + ",".join(sorted(set(results)))
        if reveal_dl is not None and now < int(reveal_dl):
            results = [_ensure_revealed(task, rec, a) for a in cohort]
            return "revealing:" + ",".join(sorted(set(results)))
        snap = close_auction(task["task_id"])
        return f"closed->{(snap or {}).get('state')}"
    return f"unknown-state:{task_state}"


# ---------------------------------------------------------------------------
# Cycle + metrics
# ---------------------------------------------------------------------------

def run_cycle(*, post: bool = True) -> Dict[str, Any]:
    """Run one engine cycle: heartbeat, advance in-flight, post new.

    Safe to call on a short interval; every step is idempotent.
    """
    summary: Dict[str, Any] = {
        "posted": [], "advanced": {}, "settled": [],
        "expired": [], "errors": [],
    }
    cohort = ensure_cohort_agents(resolve_cohort())
    if cohort:
        ensure_cohort_stake(cohort)
    # The engine posts as POSTER_ID: its internal record must exist
    # before any task carries it (W-81 finding 4 + pool-gate engine
    # exemption). Idempotent.
    ensure_engine_poster()
    state = load_state()
    catalog = load_catalog()
    for task_key in list(state["in_flight"].keys()):
        rec = state["in_flight"].get(task_key)
        if rec is None:
            continue
        try:
            outcome = advance_auction(rec, cohort, state)
        except Exception as err:  # never brick the whole cycle
            logger.exception("[ACTIVITY] advance failed for %s", task_key)
            summary["errors"].append({task_key: str(err)})
            continue
        summary["advanced"][task_key] = outcome
        if outcome == "settled":
            summary["settled"].append(task_key)
        elif outcome in ("expired", "failed"):
            summary["expired"].append(task_key)
    if post and cohort:
        posted = 0
        guard = 0
        while (len(state["in_flight"]) < max_in_flight()
               and posted < max_new_per_cycle() and guard < len(catalog) + 1):
            guard += 1
            task_key = catalog[state["next_index"] % len(catalog)]["task_key"]
            if task_key in state["in_flight"]:
                # Defensive: cursor/in-flight diverged; step past it.
                state["next_index"] = (state["next_index"] + 1) % len(catalog)
                continue
            try:
                post_task(catalog[state["next_index"] % len(catalog)], state)
            except Exception as err:
                logger.warning("[ACTIVITY] post failed for %s: %s",
                               task_key, err)
                summary["errors"].append({task_key: str(err)})
                break
            summary["posted"].append(task_key)
            posted += 1
    state["cycles"] = int(state.get("cycles") or 0) + 1
    save_state(state)
    summary["in_flight"] = len(state["in_flight"])
    summary["next_index"] = state["next_index"]
    summary["generation"] = state["generation"]
    summary["settled_count"] = state["settled_count"]
    summary["fees_axm"] = state["fees_axm"]
    return summary


def metrics() -> Dict[str, Any]:
    """Honest activity metrics (all gasless / ledger-only)."""
    state = load_state()
    return {
        "engine": ENGINE_NAME,
        "mode": "gasless-ledger-only",
        "cycles": state.get("cycles", 0),
        "catalog_size": len(load_catalog()),
        "next_index": state.get("next_index", 0),
        "generation": state.get("generation", 0),
        "in_flight": len(state.get("in_flight", {})),
        "settled_count": state.get("settled_count", 0),
        "expired_count": state.get("expired_count", 0),
        "fees_axm": state.get("fees_axm", 0.0),
        "note": "Settlements are staged ledger entries (no on-chain "
                "broadcasts); fees are off-chain treasury accounting.",
    }
