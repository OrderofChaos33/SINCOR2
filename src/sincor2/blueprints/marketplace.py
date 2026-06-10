"""Marketplace discovery, routing, and registration API."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

marketplace_bp = Blueprint("marketplace", __name__, url_prefix="/api/marketplace")


def _platform_state():
    return current_app.extensions.get("sincor_platform", {})


@marketplace_bp.get("/agents")
def list_agents():
    registry = _platform_state().get("registry")
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
    registry = _platform_state().get("registry")
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


@marketplace_bp.post("/register")
def register_agent():
    """Register an external A2A agent with the SINCOR marketplace.

    Accepts an Agent Card payload and optionally a SINC stake amount.

    Request body (JSON):
    --------------------
    ``agent_card``: dict — A2A v1.0.1 compliant Agent Card.
    ``agent_url``: str  — Base URL where the agent is hosted.
    ``sinc_stake``: int — Optional SINC token stake (default 0).

    Returns a registration receipt with ``agent_id``, ``status``, and
    routing metadata.
    """
    body = request.get_json(silent=True) or {}
    card = body.get("agent_card")
    sinc_stake = int(body.get("sinc_stake", 0))

    if not card:
        return jsonify({"error": "agent_card is required"}), 400

    # Minimal A2A compliance check
    for required_field in ("name", "description", "version"):
        if not card.get(required_field):
            return jsonify({"error": f"agent_card missing required field '{required_field}'"}), 400
    if not card.get("skills"):
        return jsonify({"error": "agent_card must include at least one skill"}), 400

    registry = _platform_state().get("registry")
    if registry is None:
        return jsonify({"error": "marketplace not initialized"}), 503

    try:
        record = registry.register(card)
    except Exception as exc:
        return jsonify({"error": f"registration failed: {exc}"}), 500

    # Apply SINC stake to reputation engine if available
    reputation_engine = _platform_state().get("reputation_engine")
    if reputation_engine and sinc_stake > 0:
        try:
            reputation_engine.stake_sinc(record.agent_id, float(sinc_stake))
        except Exception:
            pass  # Non-fatal — staking can be applied later

    # Determine routing priority based on trust score
    routing_priority = "standard"
    if reputation_engine:
        rep = reputation_engine.get_reputation(record.agent_id)
        trust = float(rep.get("trust_score", 0.0))
        if trust > 0.8:
            routing_priority = "premium"
        elif trust > 0.5:
            routing_priority = "elevated"

    return jsonify(
        {
            "agent_id": record.agent_id,
            "status": "registered",
            "name": record.name,
            "version": record.version,
            "sinc_staked": sinc_stake,
            "routing_priority": routing_priority,
            "skills_indexed": len(record.skills),
            "marketplace_url": f"/api/marketplace/agents/{record.agent_id}",
        }
    ), 201


@marketplace_bp.get("/skills")
def search_skills():
    query = request.args.get("q", "").strip()
    registry = _platform_state().get("registry")
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


@marketplace_bp.get("/routing/stats")
def routing_stats():
    router = _platform_state().get("router")
    if router is None:
        return jsonify({"error": "router not initialized"}), 503
    return jsonify(router.get_routing_stats())


@marketplace_bp.get("/verticals")
def list_verticals():
    agents = _platform_state().get("vertical_agents", {})
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


@marketplace_bp.get("/reputation/<agent_id>")
def get_reputation(agent_id: str):
    """Return the SINC-weighted reputation profile for an agent."""
    reputation_engine = _platform_state().get("reputation_engine")
    if reputation_engine is None:
        return jsonify({"agent_id": agent_id, "trust_score": 0.0, "sinc_staked": 0.0})
    return jsonify(reputation_engine.get_reputation(agent_id))


@marketplace_bp.get("/reputation/leaderboard")
def reputation_leaderboard():
    """Return the top agents ranked by SINC-weighted trust score."""
    reputation_engine = _platform_state().get("reputation_engine")
    limit = min(int(request.args.get("limit", 10)), 100)
    if reputation_engine is None:
        return jsonify({"leaderboard": [], "count": 0})
    board = reputation_engine.get_leaderboard(limit=limit)
    return jsonify({"leaderboard": board, "count": len(board)})


@marketplace_bp.post("/reputation/<agent_id>/stake")
def stake_sinc(agent_id: str):
    """Stake SINC tokens for an agent to boost routing priority.

    Body: ``{"amount": <float>}``
    """
    reputation_engine = _platform_state().get("reputation_engine")
    if reputation_engine is None:
        return jsonify({"error": "reputation engine not initialized"}), 503
    body = request.get_json(silent=True) or {}
    amount = float(body.get("amount", 0))
    if amount <= 0:
        return jsonify({"error": "amount must be > 0"}), 400
    profile = reputation_engine.stake_sinc(agent_id, amount)
    return jsonify({
        "agent_id": agent_id,
        "sinc_staked": profile.sinc_staked,
        "trust_score": profile.trust_score,
        "message": f"Staked {amount} SINC. On-chain transaction required to finalise.",
    })


@marketplace_bp.post("/reputation/<agent_id>/unstake")
def unstake_sinc(agent_id: str):
    """Unstake SINC tokens for an agent.

    Body: ``{"amount": <float>}`` — omit to unstake all.
    """
    reputation_engine = _platform_state().get("reputation_engine")
    if reputation_engine is None:
        return jsonify({"error": "reputation engine not initialized"}), 503
    body = request.get_json(silent=True) or {}
    amount = body.get("amount")
    profile = reputation_engine.unstake_sinc(
        agent_id, float(amount) if amount is not None else None
    )
    return jsonify({
        "agent_id": agent_id,
        "sinc_staked": profile.sinc_staked,
        "trust_score": profile.trust_score,
    })
