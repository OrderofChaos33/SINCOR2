"""
SINCOR2 Command Center Blueprint

Provides all backend routes for the live Command Center dashboard:

  GET  /command-center                        → dashboard HTML
  GET  /api/command-center/market-feed        → SSE stream (live tasks/bids/awards)
  GET  /api/command-center/agents             → agent roster with persona + merit
  GET  /api/command-center/agents/<id>/memory → agent task history
  GET  /api/command-center/killswitch         → token usage for every agent
  POST /api/command-center/killswitch/<id>    → kill or reinstate an agent
  POST /api/command-center/grade-task         → submit quality grade for a task
  GET  /api/command-center/market-snapshot    → one-shot market overview (JSON)
"""

from __future__ import annotations

import glob
import json
import logging
import os
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    render_template,
    request,
    stream_with_context,
)

logger = logging.getLogger("sincor.command_center")

command_center_bp = Blueprint(
    "command_center",
    __name__,
    url_prefix="",
)

# ---------------------------------------------------------------------------
# SSE event bus — a single in-process pub/sub so every subscriber gets every
# market event without polling the filesystem.
# ---------------------------------------------------------------------------

class _EventBus:
    """Simple fan-out event bus for SSE streams."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: List[queue.Queue] = []

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def publish(self, event_type: str, data: Dict[str, Any]) -> None:
        payload = {
            "event": event_type,
            "data": data,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._subscribers.remove(q)


_bus = _EventBus()


def publish_market_event(event_type: str, data: Dict[str, Any]) -> None:
    """Publish a market event to all SSE subscribers.  Safe to call from any thread."""
    _bus.publish(event_type, data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tc():
    """Return the TokenBudgetController, creating it lazily."""
    try:
        from sincor2.token_budget_controller import get_controller
        return get_controller()
    except Exception:
        return None


def _market() -> Optional[Any]:
    """Return the TaskMarket from the app platform extensions, if available."""
    platform = current_app.extensions.get("sincor_platform", {})
    return platform.get("task_market")


def _load_agent_yamls(agents_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load all E-*.yaml agent definitions and return parsed dicts."""
    try:
        import yaml as _yaml
    except ImportError:
        return []

    if agents_dir is None:
        # Try to find agents dir relative to repo root
        here = Path(__file__).resolve()
        for candidate in [
            here.parent.parent.parent.parent / "agents",
            Path("agents"),
        ]:
            if candidate.is_dir():
                agents_dir = str(candidate)
                break
        else:
            return []

    results = []
    for path in sorted(glob.glob(os.path.join(str(agents_dir), "E-*.yaml"))):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = _yaml.safe_load(fh)
            if isinstance(data, dict):
                results.append(data)
        except Exception as exc:
            logger.debug("[CC] skipping agent YAML %s: %s", path, exc)
    return results


def _career_trajectory(merit_points: int, tasks_completed: int) -> Dict[str, Any]:
    """
    Derive a simple promotion / demotion trajectory from raw stats.

    Grade tiers (merit points):
      Hatch    0–499
      Junior   500–1999
      Senior   2000–4999
      Lead     5000–9999
      Director 10000+
    """
    tiers = [
        ("Hatch", 0),
        ("Junior", 500),
        ("Senior", 2000),
        ("Lead", 5000),
        ("Director", 10000),
    ]
    current_tier = "Hatch"
    for name, threshold in tiers:
        if merit_points >= threshold:
            current_tier = name

    tier_names = [t[0] for t in tiers]
    idx = tier_names.index(current_tier)
    next_tier = tier_names[idx + 1] if idx < len(tier_names) - 1 else None
    next_threshold = tiers[idx + 1][1] if next_tier else None
    to_next = (next_threshold - merit_points) if next_threshold else 0

    return {
        "current_tier": current_tier,
        "next_tier": next_tier,
        "merit_points": merit_points,
        "to_next_promotion": max(0, to_next),
        "tasks_completed": tasks_completed,
    }


def _build_agent_roster() -> List[Dict[str, Any]]:
    """
    Combine agent YAML definitions with live reputation data into a roster list.
    """
    yamls = _load_agent_yamls()
    market = _market()
    rep_map: Dict[str, Dict[str, Any]] = {}
    if market is not None:
        try:
            rep_map = market.agent_reputation or {}
        except Exception:
            pass

    roster = []
    for agent in yamls:
        agent_id = agent.get("id", "")
        persona = agent.get("persona", {})
        traits = persona.get("traits", {})
        budgets = agent.get("budgets", {})
        rep = rep_map.get(agent_id, {})

        merit_points = int(rep.get("total_merit_earned", 0))
        tasks_completed = int(rep.get("tasks_completed", 0))

        entry = {
            "agent_id": agent_id,
            "name": agent.get("name", agent_id),
            "archetype": agent.get("archetype", ""),
            "secondary_archetype": agent.get("secondary_archetype", ""),
            "status": agent.get("status", "Hatch"),
            # OCEAN personality vector (0.0–1.0 each)
            "personality": {
                "O": float(traits.get("O", 0.5)),
                "C": float(traits.get("C", 0.5)),
                "E": float(traits.get("E", 0.5)),
                "A": float(traits.get("A", 0.5)),
                "N": float(traits.get("N", 0.5)),
            },
            "style": {
                "risk": float(persona.get("style", {}).get("risk", 0.5)),
                "humor": float(persona.get("style", {}).get("humor", 0.5)),
                "directness": float(persona.get("style", {}).get("directness", 0.5)),
            },
            # Merit & career
            "merit_points": merit_points,
            "tasks_completed": tasks_completed,
            "success_rate": round(float(rep.get("success_rate", 1.0)) * 100, 1),
            "average_quality": round(float(rep.get("average_quality", 0.0)) * 100, 1),
            "career": _career_trajectory(merit_points, tasks_completed),
            # Budget
            "daily_tokens": int(budgets.get("daily_tokens", 50000)),
            "specializations": list(agent.get("specializations", [])),
            "competencies": list(rep.get("competencies", [])),
        }
        roster.append(entry)

    return roster


def _build_market_snapshot() -> Dict[str, Any]:
    """Return a point-in-time snapshot of the task market."""
    market = _market()
    if market is None:
        return {
            "active_tasks": [],
            "stats": {},
            "recent_transactions": [],
        }
    try:
        overview = market.get_market_overview()
    except Exception as exc:
        logger.warning("[CC] market.get_market_overview failed: %s", exc)
        overview = {}

    # Hydrate bids for each active task
    active_tasks_detail = []
    try:
        for task_id, task_obj in (market.active_tasks or {}).items():
            try:
                from dataclasses import asdict
                task_dict = asdict(task_obj)
                for k, v in task_dict.items():
                    if hasattr(v, "value"):
                        task_dict[k] = v.value
                bids_raw = market.get_task_bids(task_id)
                bids_list = []
                for b in bids_raw:
                    try:
                        bd = asdict(b)
                        for k, v in bd.items():
                            if hasattr(v, "value"):
                                bd[k] = v.value
                        bids_list.append(bd)
                    except Exception:
                        pass
                task_dict["bids"] = bids_list
                active_tasks_detail.append(task_dict)
            except Exception:
                pass
    except Exception:
        pass

    return {
        "active_tasks": active_tasks_detail,
        "stats": overview.get("market_stats", {}),
        "recent_transactions": overview.get("recent_transactions", []),
        "agent_count": overview.get("agent_count", 0),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@command_center_bp.get("/command-center")
def command_center_dashboard():
    """Render the Command Center dashboard."""
    return render_template("command_center.html")


# --- SSE: Live Market Feed ---

@command_center_bp.get("/api/command-center/market-feed")
def market_feed_sse():
    """
    Server-Sent Events stream delivering live market events to the dashboard.
    Events include: task_posted, bid_submitted, task_awarded, task_completed,
    agent_killed, agent_reinstated, heartbeat.
    """
    q = _bus.subscribe()

    @stream_with_context
    def generate() -> Generator[str, None, None]:
        # Send initial snapshot on connect
        try:
            snapshot = _build_market_snapshot()
            yield f"event: snapshot\ndata: {json.dumps(snapshot)}\n\n"
        except Exception as exc:
            logger.warning("[CC] SSE initial snapshot failed: %s", exc)

        heartbeat_interval = 20  # seconds
        last_hb = time.time()

        while True:
            try:
                try:
                    event = q.get(timeout=5)
                    yield (
                        f"event: {event['event']}\n"
                        f"data: {json.dumps(event)}\n\n"
                    )
                except queue.Empty:
                    pass  # no event — check heartbeat

                if time.time() - last_hb >= heartbeat_interval:
                    yield "event: heartbeat\ndata: {}\n\n"
                    last_hb = time.time()

            except GeneratorExit:
                break
            except Exception as exc:
                logger.warning("[CC] SSE generator error: %s", exc)
                break

        _bus.unsubscribe(q)

    return Response(
        generate(),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# --- Agent Roster ---

@command_center_bp.get("/api/command-center/agents")
def get_agents():
    """Return full agent roster with personality vectors, merit, career trajectory."""
    try:
        roster = _build_agent_roster()
        return jsonify({"agents": roster, "count": len(roster)})
    except Exception as exc:
        logger.error("[CC] get_agents failed: %s", exc)
        return jsonify({"agents": [], "count": 0, "error": "internal_error"}), 500


@command_center_bp.get("/api/command-center/agents/<agent_id>/memory")
def get_agent_memory(agent_id: str):
    """
    Return an agent's task history from the memory system.
    Reads the agent's episodic SQLite store if available.
    """
    try:
        # Try to load from memory system
        from sincor2.memory_system import MemorySystem
        # Find memory dir
        here = Path(__file__).resolve()
        memory_candidates = [
            here.parent.parent.parent.parent / "memory",
            Path("memory"),
        ]
        memory_dir = next(
            (str(c) for c in memory_candidates if c.is_dir()), "memory"
        )
        ms = MemorySystem(agent_id=agent_id, memory_dir=memory_dir)

        # Pull recent episodic events
        try:
            events_raw = ms.query_episodes(limit=50)
            events = [e if isinstance(e, dict) else (e.__dict__ if hasattr(e, '__dict__') else str(e)) for e in events_raw]
        except Exception:
            events = []

        # Fallback: scan the market transactions file
        task_history = []
        market = _market()
        if market is not None:
            try:
                tx_file = getattr(market, "transactions_file", None)
                if tx_file and Path(tx_file).exists():
                    with open(tx_file, "r", encoding="utf-8") as fh:
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                tx = json.loads(line)
                                if tx.get("winner_agent_id") == agent_id:
                                    task_history.append(tx)
                            except (json.JSONDecodeError, KeyError):
                                pass
            except Exception:
                pass

        return jsonify({
            "agent_id": agent_id,
            "episodic_events": events,
            "task_history": task_history[-50:],
        })
    except Exception as exc:
        logger.error("[CC] get_agent_memory failed agent=%s: %s", agent_id, exc)
        return jsonify({
            "agent_id": agent_id,
            "episodic_events": [],
            "task_history": [],
            "error": "internal_error",
        }), 200  # 200 — dashboard degrades gracefully


# --- Killswitch Console ---

@command_center_bp.get("/api/command-center/killswitch")
def killswitch_status():
    """Return real-time token usage for every registered agent."""
    ctrl = _tc()
    if ctrl is None:
        return jsonify({"agents": {}, "error": "controller_unavailable"}), 200

    try:
        statuses = ctrl.get_all_statuses()
        # Merge with agent roster names
        roster_map = {a["agent_id"]: a["name"] for a in _build_agent_roster()}
        for agent_id, s in statuses.items():
            s["name"] = roster_map.get(agent_id, agent_id)
        return jsonify({"agents": statuses, "count": len(statuses)})
    except Exception as exc:
        logger.error("[CC] killswitch_status failed: %s", exc)
        return jsonify({"agents": {}, "error": "internal_error"}), 500


@command_center_bp.post("/api/command-center/killswitch/<agent_id>")
def killswitch_action(agent_id: str):
    """
    Kill or reinstate an agent.

    Body JSON:
      { "action": "kill" | "reinstate", "reason": "optional string" }
    """
    ctrl = _tc()
    if ctrl is None:
        return jsonify({"ok": False, "error": "controller_unavailable"}), 503

    try:
        body = request.get_json(silent=True) or {}
        action = str(body.get("action", "kill")).lower()
        reason = str(body.get("reason", "operator_override"))

        if action == "kill":
            ctrl.kill_agent(agent_id, reason=reason)
            _bus.publish("agent_killed", {"agent_id": agent_id, "reason": reason})
            logger.warning("[CC] KILL issued for agent=%s reason=%s", agent_id, reason)
            return jsonify({"ok": True, "agent_id": agent_id, "action": "killed"})
        elif action == "reinstate":
            ctrl.reinstate_agent(agent_id)
            _bus.publish("agent_reinstated", {"agent_id": agent_id})
            logger.info("[CC] REINSTATE issued for agent=%s", agent_id)
            return jsonify({"ok": True, "agent_id": agent_id, "action": "reinstated"})
        else:
            return jsonify({"ok": False, "error": f"unknown action: {action}"}), 400
    except Exception as exc:
        logger.error("[CC] killswitch_action failed agent=%s: %s", agent_id, exc)
        return jsonify({"ok": False, "error": "internal_error"}), 500


# --- Quality Scoring Form ---

@command_center_bp.post("/api/command-center/grade-task")
def grade_task():
    """
    Submit a quality grade for a completed task.

    Adjusts the winning agent's merit points and bidding weight in the market.

    Body JSON:
      {
        "task_id": "...",
        "agent_id": "...",
        "grade": 0.0–10.0,          # overall quality (10 = perfect)
        "dimension_scores": {       # optional per-dimension overrides
          "accuracy": 8.5,
          "completeness": 7.0,
          ...
        },
        "comments": "optional free-text feedback"
      }
    """
    try:
        body = request.get_json(silent=True) or {}
        task_id = str(body.get("task_id", "")).strip()
        agent_id = str(body.get("agent_id", "")).strip()
        raw_grade = float(body.get("grade", 5.0))

        if not task_id or not agent_id:
            return jsonify({"ok": False, "error": "task_id and agent_id required"}), 400
        if not (0.0 <= raw_grade <= 10.0):
            return jsonify({"ok": False, "error": "grade must be 0.0–10.0"}), 400

        normalised = raw_grade / 10.0  # convert to 0.0–1.0 internal scale
        dimension_scores = body.get("dimension_scores", {})
        comments = str(body.get("comments", ""))

        # --- Update market reputation ---
        market = _market()
        merit_delta = 0
        if market is not None:
            try:
                rep = market.agent_reputation.setdefault(agent_id, {
                    "tasks_completed": 0,
                    "success_rate": 1.0,
                    "average_quality": 0.0,
                    "competencies": [],
                    "total_merit_earned": 0,
                    "specializations": {},
                })

                old_avg = float(rep.get("average_quality", 0.0))
                # Use a dedicated grades_count to track number of feedback entries,
                # separate from tasks_completed so the weighted average is correct.
                old_count = int(rep.get("grades_count", 0))
                new_count = old_count + 1
                rep["average_quality"] = (old_avg * old_count + normalised) / new_count
                rep["grades_count"] = new_count

                # Merit delta: grade relative to neutral (0.5) × 100 pts
                merit_delta = int((normalised - 0.5) * 200)
                rep["total_merit_earned"] = int(rep.get("total_merit_earned", 0)) + merit_delta

                market._save_reputation()
            except Exception as exc:
                logger.warning("[CC] grade_task reputation update failed: %s", exc)

        # --- Try quality scoring engine (fire-and-forget async call) ---
        try:
            from sincor2.quality_scoring_engine import (
                SelfImprovingQualityEngine,
                FeedbackSource,
                QualityDimension,
                QualityFeedback,
            )
            dim_map = {
                "accuracy": QualityDimension.ACCURACY,
                "completeness": QualityDimension.COMPLETENESS,
                "relevance": QualityDimension.RELEVANCE,
                "clarity": QualityDimension.CLARITY,
                "depth": QualityDimension.DEPTH,
            }
            typed_dims = {}
            for k, v in dimension_scores.items():
                dim = dim_map.get(k.lower())
                if dim:
                    typed_dims[dim] = float(v) / 10.0

            feedback = QualityFeedback(
                feedback_id=str(uuid.uuid4()),
                deliverable_id=task_id,
                source=FeedbackSource.CLIENT_DIRECT,
                dimension_scores=typed_dims,
                overall_rating=normalised,
                specific_comments=[comments] if comments else [],
                improvement_suggestions=[],
                timestamp=datetime.now(timezone.utc).isoformat(),
                feedback_reliability=0.9,
            )

            # Run the async method via asyncio without blocking the request thread
            import asyncio
            qe = SelfImprovingQualityEngine()

            def _run_async():
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(qe.add_external_feedback(task_id, feedback))
                finally:
                    loop.close()

            import threading
            t = threading.Thread(target=_run_async, daemon=True)
            t.start()
        except Exception as exc:
            logger.debug("[CC] quality_scoring_engine feedback skipped: %s", exc)

        # --- Publish event ---
        _bus.publish("task_graded", {
            "task_id": task_id,
            "agent_id": agent_id,
            "grade": raw_grade,
            "normalised": normalised,
            "merit_delta": merit_delta,
        })

        return jsonify({
            "ok": True,
            "task_id": task_id,
            "agent_id": agent_id,
            "grade": raw_grade,
            "normalised": normalised,
            "merit_delta": merit_delta,
        })

    except ValueError as exc:
        logger.warning("[CC] grade_task invalid value: %s", exc)
        return jsonify({"ok": False, "error": "invalid_grade_value"}), 400
    except Exception as exc:
        logger.error("[CC] grade_task failed: %s", exc)
        return jsonify({"ok": False, "error": "internal_error"}), 500


# --- One-shot market snapshot ---

@command_center_bp.get("/api/command-center/market-snapshot")
def market_snapshot():
    """Return a full market snapshot as JSON (polling alternative to SSE)."""
    try:
        snapshot = _build_market_snapshot()
        return jsonify(snapshot)
    except Exception as exc:
        logger.error("[CC] market_snapshot failed: %s", exc)
        return jsonify({"error": "internal_error"}), 500
