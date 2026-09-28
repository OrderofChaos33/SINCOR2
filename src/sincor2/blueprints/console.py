"""Unified console pages (read-only).

Serves the new dashboard-overhaul pages on both Flask apps (sincor2.app and
sincor2.mvp_app, the latter being what production actually runs):

  GET /agents  -> live agent roster   (data: GET /api/a2a/agents)
  GET /tasks   -> live task board     (data: GET /v1/a2a/tasks)
  GET /pool    -> live bounty pool    (data: GET /v1/a2a/pool)

Presentation only: no money paths, no auth changes, no API semantics touched.
All data is fetched client-side; on failure the pages render "unknown",
never fake-healthy numbers.
"""
from __future__ import annotations

from flask import Blueprint, redirect, render_template

console_bp = Blueprint("console", __name__)


@console_bp.get("/dashboards")
def dashboards_menu_redirect():
    """Retired card menu -> unified command center.

    dashboards_menu.html is kept on disk but no longer linked anywhere.
    """
    return redirect("/command-center", code=302)


@console_bp.get("/agents")
def agents_page():
    """Agent fleet - searchable roster from the live A2A registry."""
    return render_template("agents.html", nav_active="agents")


@console_bp.get("/tasks")
def tasks_page():
    """Task board - live marketplace listings."""
    return render_template("tasks.html", nav_active="tasks")


@console_bp.get("/pool")
def pool_page():
    """Bounty pool - live AXM ledger panel."""
    return render_template("pool.html", nav_active="pool")
