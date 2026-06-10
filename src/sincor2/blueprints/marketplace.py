"""Marketplace discovery and routing API."""

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