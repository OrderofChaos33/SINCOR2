"""Additive Contract-Net API. Does not replace /api/marketplace first-price routes."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from flask import Blueprint, current_app, jsonify, request

from marketplace.contract_net import (
    ContractNetConfig,
    ContractNetEngine,
    demo_roster,
    demo_tasks,
    task_from_dict,
)
from marketplace.contract_net.types import clamp_invite_k

logger = logging.getLogger(__name__)

contract_net_bp = Blueprint("contract_net", __name__, url_prefix="/api/contract-net")


def _engine() -> ContractNetEngine:
    platform = current_app.extensions.get("sincor_platform") or {}
    engine = platform.get("contract_net")
    if engine is None:
        engine = ContractNetEngine()
        platform = dict(platform)
        platform["contract_net"] = engine
        current_app.extensions["sincor_platform"] = platform
    return engine


def _config_from_body(body: Dict[str, Any]) -> Optional[ContractNetConfig]:
    if not any(key in body for key in ("invite_k", "epsilon", "eval_tokens_per_bid")):
        return None
    invite_k = clamp_invite_k(int(body.get("invite_k", 4)))
    epsilon = float(body.get("epsilon", 0.12))
    epsilon = min(0.15, max(0.10, epsilon))
    return ContractNetConfig(invite_k=invite_k, epsilon=epsilon)


@contract_net_bp.get("/health")
def health():
    engine = _engine()
    return jsonify(
        {
            "ok": True,
            "mechanism": "vickrey-second-price",
            "invite_k": engine.config.invite_k,
            "epsilon": engine.config.epsilon,
            "chain_id": engine.config.chain_id,
            "verifying_contract": engine.config.verifying_contract,
            "domain": {
                "name": engine.config.domain_name,
                "version": engine.config.domain_version,
            },
        }
    )


@contract_net_bp.get("/roster")
def roster():
    agents = [
        {
            "agent_id": agent.agent_id,
            "name": agent.name,
            "skills": list(agent.skills),
            "wallet": agent.wallet,
            "tasks_completed": agent.tasks_completed,
            "success_rate": agent.success_rate,
            "true_min_price": agent.true_min_price,
            "is_junior": agent.is_junior or agent.tasks_completed < 3,
        }
        for agent in demo_roster()
    ]
    return jsonify({"agents": agents, "count": len(agents)})


@contract_net_bp.get("/tasks")
def tasks():
    payload = [
        {
            "task_id": task.task_id,
            "goal": task.goal,
            "requirements": list(task.requirements),
            "budget_tokens": task.budget_tokens,
            "max_price": task.max_price,
        }
        for task in demo_tasks()
    ]
    return jsonify({"tasks": payload, "count": len(payload)})


@contract_net_bp.get("/stats")
def stats():
    return jsonify(_engine().stats())


@contract_net_bp.get("/history")
def history():
    limit = min(int(request.args.get("limit", 25)), 100)
    records = _engine().history()[-limit:]
    return jsonify(
        {
            "auctions": [record.award.to_dict() for record in records],
            "count": len(records),
        }
    )


@contract_net_bp.post("/auctions")
def run_auction():
    """Run one cosine-filtered Vickrey round over the demo roster (or supplied agents)."""
    body = request.get_json(silent=True) or {}
    extra_config = _config_from_body(body)
    engine = _engine()
    if extra_config is not None:
        engine = ContractNetEngine(extra_config)

    task_payload = body.get("task") or {}
    if body.get("task_id") and not task_payload:
        try:
            from marketplace.contract_net.roster import task_by_id

            task = task_by_id(str(body["task_id"]))
        except KeyError:
            return jsonify({"error": f"unknown task_id {body.get('task_id')!r}"}), 404
    elif task_payload:
        task = task_from_dict(task_payload)
    else:
        task = demo_tasks()[0]

    seed = body.get("seed")
    if seed is not None:
        seed = int(seed)
    force_junior = body.get("force_junior")
    if force_junior is not None:
        force_junior = bool(force_junior)

    award = engine.run(
        task,
        demo_roster(),
        seed=seed,
        force_junior=force_junior,
    )
    # Keep the app-level engine history in sync when we spun a one-off config.
    if extra_config is not None:
        app_engine = _engine()
        app_engine._history.append(engine.history()[-1])  # noqa: SLF001
    return jsonify(award.to_dict())


@contract_net_bp.post("/simulate")
def simulate():
    body = request.get_json(silent=True) or {}
    rounds = min(int(body.get("rounds", 40)), 200)
    seed = int(body.get("seed", 7))
    extra_config = _config_from_body(body)
    engine = ContractNetEngine(extra_config or _engine().config)
    awards = engine.run_many(demo_tasks(), demo_roster(), rounds=rounds, seed=seed)
    app_engine = _engine()
    app_engine._history.extend(engine.history())  # noqa: SLF001
    return jsonify(
        {
            "rounds": rounds,
            "stats": engine.stats(),
            "awards": [award.to_dict() for award in awards[-10:]],
        }
    )
