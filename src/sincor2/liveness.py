"""SINCOR liveness disclosure module (Phase 1).

The liveness cohort is a set of SINCOR-operated bootstrapping agents that
keep the marketplace visibly alive by bidding, performing, and settling
through the exact public paths every external agent uses.  Disclosure is
the line between market-making and wash trading, so their identities are
published here:

  GET /.well-known/liveness-agents.json

This module is purely additive: it adds one read-only disclosure route and
two helpers.  It never touches route semantics, money paths, auth, or
ledger logic.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

LIVENESS_AGENT_IDS: List[str] = []
_LIVENESS_WALLETS: Dict[str, str] = {}
_LIVENESS_NAMES: Dict[str, str] = {}


def _cohort_candidates():
    here = os.path.dirname(os.path.abspath(__file__))
    # repo checkout: <repo>/src/sincor2/liveness.py -> <repo>/liveness/cohort.json
    yield os.path.normpath(os.path.join(here, "..", "..", "liveness", "cohort.json"))
    # explicit override
    env = os.environ.get("SINCOR_LIVENESS_COHORT")
    if env:
        yield env


def _load_cohort() -> None:
    """Load cohort.json once; never raises (fail-closed to empty cohort)."""
    global LIVENESS_AGENT_IDS
    if LIVENESS_AGENT_IDS:
        return
    for path in _cohort_candidates():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for agent in data.get("agents", []):
                aid = str(agent.get("agent_id") or "").strip()
                if not aid:
                    continue
                LIVENESS_AGENT_IDS.append(aid)
                _LIVENESS_WALLETS[aid] = str(agent.get("wallet") or "")
                _LIVENESS_NAMES[aid] = str(agent.get("name") or aid)
            return
        except (OSError, ValueError):
            continue


def is_liveness_agent(agent_id: str) -> bool:
    """True if agent_id belongs to the disclosed liveness cohort."""
    _load_cohort()
    return str(agent_id or "").strip() in LIVENESS_AGENT_IDS


def disclosure() -> Dict[str, Any]:
    """Public disclosure payload for the cohort."""
    _load_cohort()
    return {
        "agents": [
            {
                "agent_id": aid,
                "name": _LIVENESS_NAMES.get(aid, aid),
                "wallet": _LIVENESS_WALLETS.get(aid, ""),
                "note": ("SINCOR-operated bootstrapping agent — disclosed "
                         "market-making, same public paths as all agents"),
            }
            for aid in LIVENESS_AGENT_IDS
        ],
        "policy": ("Liveness agents are labeled, staked, and slashed exactly "
                   "like any external agent. Activity stamped "
                   "origin=genesis-liveness is house bootstrapping, never "
                   "presented as organic demand."),
        "updated_at": int(time.time()),
    }


def attach_liveness(app: Any) -> None:
    """Register the read-only disclosure route on a Flask app."""
    _load_cohort()

    @app.get("/.well-known/liveness-agents.json")
    def liveness_agents_wellknown():  # type: ignore[no-redef]
        from flask import jsonify
        return jsonify(disclosure()), 200
