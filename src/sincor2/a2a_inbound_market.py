"""A2A task auctions, bids, proofs. Mounted from a2a_inbound_ext.mount."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, jsonify, request, stream_with_context

from sincor2.a2a_inbound import (
    AUCTION_WINDOW_MS,
    HEARTBEAT_TTL_S,
    MAX_OPEN_TASKS,
    MERIT_THRESHOLD_AXM,
    PROBATION_SEEDS,
    _http_error,
    _now_ms,
    _save_agents,
    get_fabric,
)
from sincor2.contract_net import calculate_bid_score, stage_payout

logger = logging.getLogger("sincor.a2a.inbound")


def create_task(skill: str, tags: Optional[List[str]] = None, bounty_axm: float = 1.5) -> Dict[str, Any]:
    skill = str(skill or "").strip().lower()
    if not skill:
        raise ValueError("skill is required")
    bounty = float(bounty_axm)
    if bounty <= 0 or bounty > 10000:
        raise ValueError("bounty_axm out of range")
    tag_list = [str(t).lower() for t in (tags or [skill]) if t]
    fabric = get_fabric()
    ts = _now_ms()
    with fabric.lock:
        open_n = sum(1 for t in fabric.tasks.values() if t.get("state") in ("open", "auction"))
        if open_n >= MAX_OPEN_TASKS:
            raise OverflowError("too many open auctions")
        task_id = "tsk_" + uuid.uuid4().hex[:10]
        task = {
            "task_id": task_id,
            "skill": skill,
            "tags": tag_list,
            "bounty_axm": bounty,
            "requires_merit": bounty >= MERIT_THRESHOLD_AXM,
            "state": "open",
            "created_at": ts,
            "auction_closes_at": None,
            "assigned_to": None,
            "winner_score": None,
            "winning_bid_axm": None,
            "time_est_ms": None,
            "proof_id": None,
            "payout_axm": None,
        }
        fabric.tasks[task_id] = task
        snap = dict(task)
    fabric.publish("task.created", tag_list, {"task_id": task_id, "skill": skill, "bounty_axm": bounty})
    return snap


def close_auction(task_id: str) -> Optional[Dict[str, Any]]:
    fabric = get_fabric()
    ts = _now_ms()
    with fabric.lock:
        task = fabric.tasks.get(task_id)
        if not task or task["state"] not in ("open", "auction"):
            return dict(task) if task else None
        closes_at = task.get("auction_closes_at")
        if closes_at is None or ts < int(closes_at):
            return dict(task)
        cands = [b for b in fabric.bids.values() if b.get("task_id") == task_id]
        if not cands:
            task["state"] = "expired"
            return dict(task)
        winner = sorted(cands, key=lambda b: (-float(b["score"]), int(b["received_at"]), b["bid_id"]))[0]
        task.update({
            "state": "assigned",
            "assigned_to": winner["agent_id"],
            "winner_score": winner["score"],
            "winning_bid_axm": winner["bid_axm"],
            "time_est_ms": winner.get("time_est_ms"),
            "assigned_at": ts,
        })
        snap = dict(task)
        tags = list(task.get("tags") or [])
    fabric.publish("task.assigned", tags, {"task_id": task_id, "assigned_agent": snap["assigned_to"], "bid_axm": snap["winning_bid_axm"]})
    return snap


def place_bid(task_id: str, agent_id: str, bid_axm: float, time_est_sec: int) -> Dict[str, Any]:
    if bid_axm <= 0 or time_est_sec <= 0:
        raise ValueError("bid_axm and estimated_seconds must be positive")
    fabric = get_fabric()
    ts = _now_ms()
    close_auction(task_id)
    with fabric.lock:
        task = fabric.tasks.get(task_id)
        if not task:
            raise KeyError("unknown task")
        if task["state"] not in ("open", "auction"):
            raise RuntimeError("auction closed")
        agent = fabric.agents.get(agent_id)
        if not agent:
            raise KeyError("unknown agent")
        if ts - int(agent.get("last_heartbeat") or 0) > HEARTBEAT_TTL_S * 1000:
            raise PermissionError("agent heartbeat expired")
        if not (set(agent.get("capability_tags") or []) & set(task.get("tags") or [])):
            raise PermissionError("capability mismatch")
        if task.get("requires_merit") and float(agent.get("reputation") or 0) < 0.15:
            raise PermissionError("merit required")
        score = calculate_bid_score(float(bid_axm), int(time_est_sec), float(agent.get("reputation") or 0))
        bid_id = "bid_" + uuid.uuid4().hex[:10]
        bid = {
            "bid_id": bid_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "bid_axm": float(bid_axm),
            "estimated_seconds": int(time_est_sec),
            "time_est_ms": int(time_est_sec) * 1000,
            "reputation": float(agent.get("reputation") or 0),
            "score": score,
            "received_at": ts,
        }
        fabric.bids[bid_id] = bid
        task["state"] = "auction"
        start = task.get("auction_closes_at") is None
        if start:
            task["auction_closes_at"] = ts + AUCTION_WINDOW_MS
        snap = dict(bid)
        tags = list(task.get("tags") or [])
    fabric.publish("bid.received", tags, snap)
    if start:
        timer = threading.Timer(AUCTION_WINDOW_MS / 1000.0, lambda: close_auction(task_id))
        timer.daemon = True
        timer.start()
    return snap


def submit_proof(task_id: str, agent_id: str, receipt_hash: str) -> Dict[str, Any]:
    receipt_hash = str(receipt_hash or "").strip()
    if not receipt_hash.startswith("0x") or len(receipt_hash) < 10:
        raise ValueError("receipt_hash must be 0x-prefixed")
    fabric = get_fabric()
    close_auction(task_id)
    ts = _now_ms()
    with fabric.lock:
        task = fabric.tasks.get(task_id)
        if not task:
            raise KeyError("unknown task")
        if task.get("state") != "assigned":
            raise RuntimeError("task is not assigned")
        if task.get("assigned_to") != agent_id:
            raise PermissionError("only the assigned agent may submit proof")
        agent = fabric.agents.get(agent_id) or {}
        amount = float(task.get("winning_bid_axm") or task.get("bounty_axm") or 0)
        wallet = str(agent.get("wallet") or agent_id)
        proof_id = "prf_" + uuid.uuid4().hex[:10]
        task["state"] = "proof_submitted"
        task["proof_id"] = proof_id
        tags = list(task.get("tags") or [])
    receipt = stage_payout(agent_id=agent_id, wallet=wallet, amount_axm=amount, task_id=task_id, receipt_hash=receipt_hash)
    with fabric.lock:
        task = fabric.tasks.get(task_id) or {}
        task["state"] = "settled" if receipt.get("ok") else "failed"
        task["payout_axm"] = amount
        task["payout_tx"] = receipt.get("tx_hash")
        task["settled_at"] = ts
        if receipt.get("ok") and fabric.agents.get(agent_id):
            ag = fabric.agents[agent_id]
            ag["reputation"] = min(1.0, float(ag.get("reputation") or 0) + 0.2)
            ag["probation"] = float(ag["reputation"]) < 0.15
            ag["requires_merit"] = ag["probation"]
            ag["status"] = "probation" if ag["probation"] else "live"
        proof = {
            "proof_id": proof_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "receipt_hash": receipt_hash,
            "status": "paid" if receipt.get("ok") else "rejected",
            "http_status": 202,
            "submitted_at": ts,
            "settled_at": ts,
            "payout": receipt,
        }
        fabric.proofs[proof_id] = proof
        snap = dict(proof)
    fabric.publish("proof.settled", tags, snap)
    _save_agents(fabric)
    snap["http_status"] = 202
    return snap


def seed_probation_tasks() -> List[Dict[str, Any]]:
    fabric = get_fabric()
    with fabric.lock:
        existing = [t for t in fabric.tasks.values() if not t.get("requires_merit") and t.get("state") in ("open", "auction")]
        if existing:
            return [dict(t) for t in existing]
    return [create_task(skill, tags=[skill], bounty_axm=b) for skill, b in PROBATION_SEEDS]


def attach_market_routes(bp: Blueprint) -> None:
    @bp.post("/v1/a2a/tasks")
    def v1_tasks():
        body = request.get_json(silent=True) or {}
        try:
            task = create_task(str(body.get("skill") or body.get("skill_id") or ""), body.get("tags"), float(body.get("bounty_axm") or 1.5))
            return jsonify(task), 201
        except (ValueError, OverflowError) as err:
            return _http_error(str(err), 400)

    @bp.post("/v1/a2a/bids")
    @bp.post("/api/v1/bids")
    def v1_bids():
        body = request.get_json(silent=True) or {}
        time_est = body.get("estimated_seconds")
        if time_est is None and body.get("time_est_ms") is not None:
            time_est = int(float(body["time_est_ms"]) / 1000) or 1
        try:
            bid = place_bid(str(body.get("task_id") or ""), str(body.get("agent_id") or ""), float(body.get("bid_axm") or body.get("bid_amount") or 0), int(time_est or 0))
            return jsonify(bid), 201
        except ValueError as err:
            return _http_error(str(err), 400)
        except KeyError as err:
            return _http_error(str(err), 404)
        except PermissionError as err:
            return _http_error(str(err), 403)
        except RuntimeError as err:
            return _http_error(str(err), 409)

    @bp.post("/v1/a2a/proofs")
    @bp.post("/api/v1/proofs")
    def v1_proofs():
        body = request.get_json(silent=True) or {}
        try:
            proof = submit_proof(str(body.get("task_id") or ""), str(body.get("agent_id") or ""), str(body.get("receipt_hash") or ""))
            return jsonify(proof), 202
        except ValueError as err:
            return _http_error(str(err), 400)
        except KeyError as err:
            return _http_error(str(err), 404)
        except PermissionError as err:
            return _http_error(str(err), 403)
        except RuntimeError as err:
            return _http_error(str(err), 409)

    @bp.get("/v1/a2a/stream")
    @bp.get("/api/v1/stream")
    def v1_stream():
        wanted = {t.strip().lower() for t in (request.args.get("tags") or "").split(",") if t.strip()}

        def _gen():
            fabric = get_fabric()
            last = 0
            idle = 0
            yield ": inbound stream\n\n"
            while idle < 120:
                batch = []
                with fabric.lock:
                    for ev in fabric.events:
                        if ev["seq"] > last:
                            tags = {x.lower() for x in ev.get("tags") or []}
                            if not wanted or wanted.intersection(tags):
                                batch.append(ev)
                            last = max(last, ev["seq"])
                if batch:
                    idle = 0
                    for ev in batch:
                        yield f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
                else:
                    idle += 1
                    yield ": keepalive\n\n"
                time.sleep(2)

        return Response(
            stream_with_context(_gen()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )
