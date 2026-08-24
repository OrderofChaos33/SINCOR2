"""Additive Cortex API: memory gate, optimistic settlement, EigenTrust merit."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from marketplace.cortex_sim import run_all, run_memory_scenario, run_merit_scenario, run_settlement_scenario
from marketplace.memory_gate import MemoryGate, ScratchStep
from marketplace.merit import MeritEngine
from marketplace.optimistic import OptimisticBatcher

cortex_bp = Blueprint("cortex", __name__, url_prefix="/api/cortex")

_gate = MemoryGate()
_batcher = OptimisticBatcher()
_merit = MeritEngine()


@cortex_bp.get("/health")
def health():
    return jsonify(
        {
            "ok": True,
            "surface": "cortex",
            "memory": {"semantic": len(_gate.semantic), "episodic": _gate.episodic.count()},
            "settlement": _batcher.stats(),
            "merit_agents": len(_merit.leaderboard()),
        }
    )


@cortex_bp.post("/memory/run")
def memory_run():
    body = request.get_json(silent=True) or {}
    now = float(body.get("now", 1_700_000_000.0))
    return jsonify(run_memory_scenario(now=now))


@cortex_bp.post("/memory/ingest")
def memory_ingest():
    body = request.get_json(silent=True) or {}
    steps_raw = body.get("steps") or []
    if not steps_raw:
        return jsonify({"error": "steps required"}), 400
    steps = [
        ScratchStep(
            step_id=str(item.get("step_id")),
            task_id=str(item.get("task_id")),
            agent_id=str(item.get("agent_id")),
            kind=str(item.get("kind", "thought")),
            content=str(item.get("content", "")),
            status=str(item.get("status", "ok")),
            confidence=float(item.get("confidence", 1.0)),
            created_at=float(item.get("created_at", 0.0)),
            tokens=tuple(item.get("tokens") or []),
        )
        for item in steps_raw
    ]
    result = _gate.ingest(
        steps,
        merit=float(body.get("merit", 0.0)),
        summary=str(body.get("summary", "")),
        closed_at=float(body.get("closed_at", steps[-1].created_at)),
    )
    return jsonify(result.to_dict())


@cortex_bp.post("/memory/retrieve")
def memory_retrieve():
    body = request.get_json(silent=True) or {}
    tokens = body.get("tokens") or []
    now = float(body.get("now", 0.0))
    hits = _gate.semantic.retrieve(tokens, now=now, limit=int(body.get("limit", 8)))
    return jsonify({"hits": [hit.to_dict() for hit in hits], "count": len(hits)})


@cortex_bp.post("/settlement/simulate")
def settlement_simulate():
    body = request.get_json(silent=True) or {}
    return jsonify(
        run_settlement_scenario(
            now=float(body.get("now", 1_700_000_000.0)),
            block=int(body.get("block", 10_000)),
        )
    )


@cortex_bp.post("/merit/simulate")
def merit_simulate():
    return jsonify(run_merit_scenario())


@cortex_bp.get("/merit/honeypots")
def merit_honeypots():
    from dataclasses import asdict

    return jsonify({"tasks": [asdict(task) for task in _merit.honeypot_catalog()]})


@cortex_bp.post("/simulate")
def simulate_all():
    return jsonify(run_all())
