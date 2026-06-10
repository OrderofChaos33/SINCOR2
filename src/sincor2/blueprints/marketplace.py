"""Marketplace discovery, routing, reputation, settlement, and task execution API."""

from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict

from flask import Blueprint, current_app, jsonify, request

from sincor2.vertical_dispatch import dispatch_via_router, dispatch_vertical_task

logger = logging.getLogger(__name__)

marketplace_bp = Blueprint("marketplace", __name__, url_prefix="/api/marketplace")


def _platform() -> Dict[str, Any]:
    return current_app.extensions.get("sincor_platform", {})


# ---------------------------------------------------------------------------
# Agent discovery
# ---------------------------------------------------------------------------

@marketplace_bp.get("/agents")
def list_agents():
    registry = _platform().get("registry")
    if registry is None:
        return jsonify({"agents": [], "count": 0})
    agents = [
        {
            "agent_id": record.agent_id,
            "name": record.name,
            "description": record.description,
            "version": record.version,
            "tags": record.tags,
            "skills": [skill.get("id") for skill in record.skills],
        }
        for record in registry.list_all()
    ]
    return jsonify({"agents": agents, "count": len(agents)})


@marketplace_bp.get("/agents/<agent_id>")
def get_agent(agent_id: str):
    registry = _platform().get("registry")
    if registry is None:
        return jsonify({"error": "marketplace not initialized"}), 503
    record = registry.get(agent_id)
    if record is None:
        return jsonify({"error": f"agent '{agent_id}' not found"}), 404
    return jsonify(
        {
            "agent_id": record.agent_id,
            "name": record.name,
            "description": record.description,
            "version": record.version,
            "endpoint": record.endpoint,
            "tags": record.tags,
            "skills": record.skills,
            "provider": record.provider,
        }
    )


@marketplace_bp.get("/skills")
def search_skills():
    query = request.args.get("q", "").strip()
    registry = _platform().get("registry")
    if registry is None:
        return jsonify({"matches": [], "count": 0})
    if not query:
        matches = [
            {
                "agent_id": record.agent_id,
                "agent_name": record.name,
                "skill_id": skill.get("id"),
                "skill_name": skill.get("name"),
                "tags": skill.get("tags", []),
            }
            for record in registry.list_all()
            for skill in record.skills
        ]
    else:
        matches = []
        for record in registry.search_by_skill(query):
            for skill in record.skills:
                haystack = " ".join(
                    [
                        skill.get("id", ""),
                        skill.get("name", ""),
                        skill.get("description", ""),
                        " ".join(skill.get("tags", [])),
                    ]
                ).lower()
                if query.lower() in haystack:
                    matches.append(
                        {
                            "agent_id": record.agent_id,
                            "agent_name": record.name,
                            "skill_id": skill.get("id"),
                            "skill_name": skill.get("name"),
                            "tags": skill.get("tags", []),
                        }
                    )
    return jsonify({"matches": matches, "count": len(matches)})


# ---------------------------------------------------------------------------
# Reputation
# ---------------------------------------------------------------------------

@marketplace_bp.get("/agents/<agent_id>/reputation")
def get_agent_reputation(agent_id: str):
    """Return the reputation profile for a specific agent."""
    reputation = _platform().get("reputation")
    if reputation is None:
        return jsonify({"error": "reputation engine not initialized"}), 503
    return jsonify(reputation.get_reputation(agent_id))


@marketplace_bp.get("/leaderboard")
def leaderboard():
    """Return the top agents ranked by trust score."""
    reputation = _platform().get("reputation")
    if reputation is None:
        return jsonify({"leaderboard": [], "count": 0})
    limit = min(int(request.args.get("limit", 10)), 100)
    board = reputation.get_leaderboard(limit=limit)
    return jsonify({"leaderboard": board, "count": len(board)})


@marketplace_bp.post("/tasks/<task_reference>/outcome")
def record_task_outcome(task_reference: str):
    """Record a task outcome to update agent reputation.

    Body:
        agent_id (str): the agent that executed the task
        success (bool): whether the task succeeded
        quality_rating (float): 1.0–5.0 quality rating
        latency_ms (int, optional): execution latency in milliseconds
    """
    reputation = _platform().get("reputation")
    if reputation is None:
        return jsonify({"error": "reputation engine not initialized"}), 503

    body = request.get_json(force=True, silent=True) or {}
    agent_id = body.get("agent_id", "").strip()
    if not agent_id:
        return jsonify({"error": "agent_id is required"}), 400

    success = bool(body.get("success", True))
    try:
        quality_rating = float(body.get("quality_rating", 3.0))
    except (TypeError, ValueError):
        return jsonify({"error": "quality_rating must be a number between 1 and 5"}), 400
    if not (1.0 <= quality_rating <= 5.0):
        return jsonify({"error": "quality_rating must be between 1.0 and 5.0"}), 400

    latency_ms = body.get("latency_ms")
    if latency_ms is not None:
        try:
            latency_ms = int(latency_ms)
        except (TypeError, ValueError):
            latency_ms = None

    profile = reputation.record_task_outcome(
        agent_id=agent_id,
        success=success,
        quality_rating=quality_rating,
        latency_ms=latency_ms,
    )

    # Release router load counter after outcome is recorded
    router = _platform().get("router")
    if router is not None:
        router.release_load(agent_id)

    logger.info(
        "Outcome recorded task=%s agent=%s success=%s quality=%.1f trust=%.4f",
        task_reference,
        agent_id,
        success,
        quality_rating,
        profile.trust_score,
    )
    from dataclasses import asdict
    return jsonify({"task_reference": task_reference, "reputation": asdict(profile)})


# ---------------------------------------------------------------------------
# Task submission (marketplace-native, synchronous)
# ---------------------------------------------------------------------------

@marketplace_bp.post("/tasks")
def submit_task():
    """Submit a task through the marketplace for synchronous execution.

    Body:
        skill_id (str): A2A skill identifier
        input (str | dict): task input (text or JSON payload)
        caller_id (str, optional): calling agent/user identifier
        preferred_tags (list[str], optional): tag hints for routing
        payer (str, optional): wallet address of payer (for settlement quote)
        amount (str, optional): settlement amount as decimal string
        token_symbol (str, optional): AXIOM or SINC (default AXIOM)
    """
    platform = _platform()
    if not platform:
        return jsonify({"error": "platform not initialized"}), 503

    body = request.get_json(force=True, silent=True) or {}
    skill_id = body.get("skill_id", "").strip()
    input_data = body.get("input", "")
    caller_id = body.get("caller_id", "anonymous")
    preferred_tags = body.get("preferred_tags", [])

    if not skill_id:
        return jsonify({"error": "skill_id is required"}), 400

    input_text = input_data if isinstance(input_data, str) else json.dumps(input_data)
    if not input_text:
        return jsonify({"error": "input is required"}), 400

    # Policy check
    policy = platform.get("policy")
    if policy is not None:
        check = policy.check_policy({"budget": float(body.get("budget", 0.0)), "retries": 0, "request_rate": 0.0})
        if not check["allowed"]:
            return jsonify({"error": "policy violation", "violations": check["violations"]}), 422

    # Route to best agent
    router = platform.get("router")
    if router is None:
        return jsonify({"error": "router not initialized"}), 503

    decision = router.route(task_id="marketplace-task", required_skills=[skill_id], preferred_tags=preferred_tags or None)
    if decision is None:
        return jsonify({"error": f"no agent available for skill '{skill_id}'"}), 404

    # Execute via vertical dispatch or router hook
    reliability = platform.get("reliability")

    def _execute():
        vertical_result = dispatch_vertical_task(skill_id, input_text, platform)
        if vertical_result:
            return vertical_result
        return dispatch_via_router("marketplace-task", skill_id, input_text, platform)

    try:
        if reliability is not None:
            result = reliability.call_with_breaker(f"vertical:{skill_id}", _execute)
        else:
            result = _execute()
    except Exception as exc:
        logger.exception("marketplace task execution failed skill=%s", skill_id)
        return jsonify({"error": str(exc), "skill_id": skill_id}), 500

    output, error = result if result else (None, "no output produced")

    # Build settlement quote if payer provided
    settlement_quote = None
    payer = body.get("payer", "").strip()
    raw_amount = body.get("amount", "1.0")
    token_symbol = body.get("token_symbol", "AXIOM")
    settlement = platform.get("settlement")
    if payer and settlement is not None:
        try:
            amount_decimal = Decimal(str(raw_amount))
            quote = settlement.create_quote(
                task_reference=decision.task_id,
                payer=payer,
                payee=decision.agent_id,
                amount=amount_decimal,
                token_symbol=token_symbol,
            )
            from dataclasses import asdict
            settlement_quote = asdict(quote)
        except (InvalidOperation, Exception) as exc:
            logger.warning("settlement quote failed: %s", exc)

    response: dict = {
        "skill_id": skill_id,
        "agent_id": decision.agent_id,
        "trust_score": decision.trust_score,
        "matched_skills": decision.matched_skills,
    }
    if error:
        response["error"] = error
        response["status"] = "failed"
    else:
        response["output"] = output
        response["status"] = "completed"
    if settlement_quote:
        response["settlement_quote"] = settlement_quote

    return jsonify(response), 200 if not error else 500


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------

@marketplace_bp.post("/settlement/confirm")
def confirm_settlement():
    """Confirm an off-chain or on-chain payment for a settlement quote.

    Body:
        quote_id (str): quote to confirm
        tx_hash (str): on-chain tx hash (or off-chain reference)
        confirmed_amount (str): actual amount paid as decimal string
    """
    platform = _platform()
    settlement = platform.get("settlement")
    if settlement is None:
        return jsonify({"error": "settlement not initialized"}), 503

    body = request.get_json(force=True, silent=True) or {}
    quote_id = body.get("quote_id", "").strip()
    tx_hash = body.get("tx_hash", "").strip()
    raw_amount = body.get("confirmed_amount", "")

    if not quote_id or not tx_hash or not raw_amount:
        return jsonify({"error": "quote_id, tx_hash, and confirmed_amount are required"}), 400

    try:
        confirmed_amount = Decimal(str(raw_amount))
    except InvalidOperation:
        return jsonify({"error": "confirmed_amount must be a valid decimal number"}), 400

    if quote_id not in settlement.quotes:
        return jsonify({"error": f"quote '{quote_id}' not found"}), 404

    try:
        record = settlement.confirm_payment(
            quote_id=quote_id,
            tx_hash=tx_hash,
            confirmed_amount=confirmed_amount,
        )
    except Exception as exc:
        logger.exception("settlement confirm failed quote=%s", quote_id)
        return jsonify({"error": str(exc)}), 500

    from dataclasses import asdict
    return jsonify({"settlement": asdict(record)})


@marketplace_bp.get("/settlement/stats")
def settlement_stats():
    """Return settlement summary statistics."""
    platform = _platform()
    settlement = platform.get("settlement")
    if settlement is None:
        return jsonify({"error": "settlement not initialized"}), 503
    return jsonify(
        {
            "open_quotes": sum(1 for q in settlement.quotes.values() if q.status == "quoted"),
            "total_quotes": len(settlement.quotes),
            "total_settlements": len(settlement.settlements),
            "treasury_journal_entries": len(settlement.treasury_journal),
        }
    )


# ---------------------------------------------------------------------------
# Routing & verticals info
# ---------------------------------------------------------------------------

@marketplace_bp.get("/routing/stats")
def routing_stats():
    router = _platform().get("router")
    if router is None:
        return jsonify({"error": "router not initialized"}), 503
    return jsonify(router.get_routing_stats())


@marketplace_bp.get("/verticals")
def list_verticals():
    agents = _platform().get("vertical_agents", {})
    payload = [
        {
            "name": agent.name,
            "version": agent.version,
            "capabilities": agent.capabilities,
            "tags": agent.tags,
            "agent_card": agent.get_agent_card(),
        }
        for agent in agents.values()
    ]
    return jsonify({"verticals": payload, "count": len(payload)})