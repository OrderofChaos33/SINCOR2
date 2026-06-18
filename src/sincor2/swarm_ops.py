"""
Operational swarm — maps star-named agents to real autonomous tasks.

The 43 YAML agents are specs; this module runs concrete work units on a schedule
and logs evidence to data/swarm_activity.json for /api/ops/swarm-status.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sincor2.data_paths import data_dir, project_root

logger = logging.getLogger("sincor2.swarm_ops")

# Agent id → real task (no simulated tool calls)
SWARM_TASKS: list[dict[str, str]] = [
    {
        "agent_id": "E-auriga-01",
        "agent_name": "Auriga",
        "archetype": "Scout",
        "task": "chain_snapshot",
        "description": "On-chain inventory + floor price snapshot",
    },
    {
        "agent_id": "E-polaris-09",
        "agent_name": "Polaris",
        "archetype": "Synthesizer",
        "task": "launch_content_draft",
        "description": "Draft launch content to human review queue",
    },
    {
        "agent_id": "E-fomalhaut-22",
        "agent_name": "Fomalhaut",
        "archetype": "Negotiator",
        "task": "partner_outreach_batch",
        "description": "Generate partner DM batch for manual send",
    },
    {
        "agent_id": "E-alkaid-28",
        "agent_name": "Alkaid",
        "archetype": "Caretaker",
        "task": "revenue_digest",
        "description": "Orders DB revenue + pipeline summary",
    },
    {
        "agent_id": "E-canopus-15",
        "agent_name": "Canopus",
        "archetype": "Builder",
        "task": "grant_opportunities",
        "description": "Refresh grant / funding target list",
    },
    {
        "agent_id": "E-sirius-08",
        "agent_name": "Sirius",
        "archetype": "Orchestrator",
        "task": "platform_health",
        "description": "A2A marketplace + vertical agent registry health",
    },
]


def _activity_path() -> Path:
    p = data_dir() / "swarm_activity.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_activity() -> dict[str, Any]:
    path = _activity_path()
    if not path.exists():
        return {"runs": [], "agents_active": 0, "last_run": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"runs": [], "agents_active": 0, "last_run": None}


def save_activity(data: dict[str, Any]) -> None:
    _activity_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def _task_chain_snapshot() -> dict[str, Any]:
    root = str(project_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    from launch_content_engine.onchain_stats import build_official_price_payload, fetch_stats

    stats = fetch_stats()
    price = build_official_price_payload(stats)
    return {
        "ok": True,
        "official_floor_usd": price.get("official_floor_usd"),
        "sinc_in_curve_m": stats.get("sinc_in_curve_m"),
        "sinc_in_safe": stats.get("sinc_in_safe"),
        "treasury": "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac",
    }


def _task_launch_content_draft() -> dict[str, Any]:
    root = str(project_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    from launch_content_engine.run_cycle import run_once

    draft_ids = run_once("agent_spotlight")
    return {"ok": True, "draft_ids": draft_ids, "review_url": "/launch/review"}


def _task_partner_outreach_batch() -> dict[str, Any]:
    from sincor2.launchpad_outreach import due_outreach as lp_due
    from sincor2.launchpad_outreach import pipeline_summary as lp_summary
    from sincor2.launchpad_outreach import write_batch_file as lp_batch
    from sincor2.partner_outreach import due_outreach, pipeline_summary, write_batch_file

    path = write_batch_file(limit=5)
    summary = pipeline_summary()
    due = due_outreach(limit=5)
    lp_path = lp_batch(limit=5)
    lp_sum = lp_summary()
    lp_due_list = lp_due(limit=5)
    return {
        "ok": True,
        "batch_file": str(path),
        "launchpad_batch_file": str(lp_path),
        "queued": summary.get("by_status", {}).get("queued", 0),
        "due_today": len(due),
        "launchpads_due": len(lp_due_list),
        "launchpads_queued": lp_sum.get("by_status", {}).get("queued", 0),
    }


def _task_revenue_digest() -> dict[str, Any]:
    from sincor2.launch_campaign import campaign_status, run_campaign_ops
    from sincor2.revenue_snapshot import fetch_live_status

    live = fetch_live_status()
    from sincor2.partner_outreach import pipeline_summary

    partners = pipeline_summary()
    campaign_ops = run_campaign_ops()
    campaign = campaign_status()
    return {
        "ok": True,
        "revenue": live.get("orders", {}),
        "live": live,
        "partners": partners,
        "campaign": {
            "phase": campaign.get("phase"),
            "predeposit_usdc_total": campaign.get("predeposit_usdc_total"),
            "milestones_achieved": sum(
                1 for m in campaign.get("milestones", []) if m.get("achieved")
            ),
            "new_milestones": campaign_ops.get("new_milestones", 0),
        },
        "campaign_url": "/launch/campaign",
    }


def _task_grant_opportunities() -> dict[str, Any]:
    from sincor2.grant_targets import list_grant_targets

    targets = list_grant_targets()
    return {"ok": True, "count": len(targets), "top": targets[:5]}


def _task_platform_health() -> dict[str, Any]:
    from sincor2.monetization_catalog import catalog_summary

    root = str(project_root())
    if root not in sys.path:
        sys.path.insert(0, root)

    cards = 0
    verticals = 0
    a2a = False
    try:
        from flask import has_app_context, current_app

        if has_app_context():
            state = current_app.extensions.get("sincor_platform", {})
            cards = state.get("registered_cards", 0)
            verticals = len(state.get("vertical_agents", {}))
            a2a = True
    except Exception:
        pass

    if not a2a:
        try:
            from marketplace.registry import AgentCardRegistry

            reg = AgentCardRegistry(storage_path=project_root() / "marketplace" / "agent_cards.json")
            cards = len(reg.list_all())
            a2a = cards > 0
        except Exception:
            pass
        try:
            from verticals.loader import instantiate_vertical_agents

            verticals = len(instantiate_vertical_agents())
        except Exception:
            pass

    catalog = catalog_summary()
    compliance = {"ok": True, "violations": 0}
    try:
        from sincor2.compliance_monitor import run_all_checks

        violations = run_all_checks()
        compliance = {"ok": len(violations) == 0, "violations": violations}
    except Exception as exc:
        compliance = {"ok": False, "error": str(exc), "violations": []}

    return {
        "ok": a2a and verticals > 0,
        "a2a_live": a2a,
        "registered_cards": cards,
        "vertical_agents": verticals,
        "monetization_live": catalog.get("live_count", 0),
        "compliance": {
            "ok": compliance.get("ok", True),
            "violations": len(compliance.get("violations") or []),
        },
        "agent_card_url": "/.well-known/agent-card.json",
        "marketplace_url": "/api/marketplace/agents",
    }


_TASK_RUNNERS = {
    "chain_snapshot": _task_chain_snapshot,
    "launch_content_draft": _task_launch_content_draft,
    "partner_outreach_batch": _task_partner_outreach_batch,
    "revenue_digest": _task_revenue_digest,
    "grant_opportunities": _task_grant_opportunities,
    "platform_health": _task_platform_health,
}


def run_swarm_cycle(tasks: list[str] | None = None) -> dict[str, Any]:
    """Execute one swarm cycle; returns summary for API/dashboard."""
    started = datetime.now(timezone.utc).isoformat()
    results: list[dict[str, Any]] = []
    task_filter = set(tasks) if tasks else None

    for spec in SWARM_TASKS:
        if task_filter and spec["task"] not in task_filter:
            continue
        entry = {
            "agent_id": spec["agent_id"],
            "agent_name": spec["agent_name"],
            "archetype": spec["archetype"],
            "task": spec["task"],
            "description": spec["description"],
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            runner = _TASK_RUNNERS[spec["task"]]
            entry["result"] = runner()
            entry["ok"] = entry["result"].get("ok", True)
            entry["status"] = "completed"
            logger.info("[SWARM] %s (%s) completed", spec["agent_name"], spec["task"])
        except Exception as e:
            entry["ok"] = False
            entry["status"] = "error"
            entry["error"] = str(e)
            logger.error("[SWARM] %s failed: %s", spec["agent_name"], e, exc_info=True)
        entry["finished_at"] = datetime.now(timezone.utc).isoformat()
        results.append(entry)

    summary = {
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "agents_run": len(results),
        "agents_ok": sum(1 for r in results if r.get("ok")),
        "results": results,
    }

    activity = load_activity()
    runs = activity.get("runs", [])
    runs.append(summary)
    activity["runs"] = runs[-50:]
    activity["last_run"] = summary["finished_at"]
    activity["agents_active"] = len(SWARM_TASKS)
    save_activity(activity)
    return summary