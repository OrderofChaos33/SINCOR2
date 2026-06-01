"""
SINAX / PTN Flask Blueprint
============================
Exposes the Proof Topology Navigator (PTN) layers as REST endpoints so that
any HTTP client — browsers, CI pipelines, external agents — can call SINAX
directly without going through the A2A JSON-RPC layer.

Routes
------
POST /api/ptn/solve        Full end-to-end proof search (all four PTN layers)
POST /api/ptn/embed        Layer 1: embed a proof state onto the manifold
POST /api/ptn/geodesic     Layer 2: compute geodesic between two proof states
POST /api/ptn/homology     Layer 3: persistent homology over a set of states
POST /api/ptn/morse        Layer 4: Morse decomposition of proof complexity
GET  /api/ptn/health       Returns 200 {"status": "ok"} if SINAX is importable

All POST endpoints accept JSON bodies; see each handler for the expected fields.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, jsonify, request

logger = logging.getLogger("sincor.sinax_bp")

sinax_bp = Blueprint("sinax", __name__, url_prefix="/api/ptn")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bad_request(msg: str) -> Response:
    return jsonify({"error": msg}), 400  # type: ignore[return-value]


def _get_navigator():
    """Return a ProofTopologyNavigator instance (imported lazily)."""
    from sincor2.sinax import ProofTopologyNavigator  # noqa: PLC0415
    return ProofTopologyNavigator()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@sinax_bp.get("/health")
def ptn_health() -> Response:
    """Return 200 if SINAX is importable and functional."""
    try:
        _get_navigator()
        return jsonify({"status": "ok", "layer": "SINAX/PTN"})
    except Exception as exc:  # pragma: no cover
        logger.error("SINAX health check failed: %s", exc)
        return jsonify({"status": "error", "detail": str(exc)}), 500  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Layer 1 — Embed
# ---------------------------------------------------------------------------

@sinax_bp.post("/embed")
def ptn_embed() -> Response:
    """
    Embed a proof state onto the manifold (Layer 1).

    Request body (JSON):
        { "state": "<proof state string>" }

    Response:
        { "source_state": "...", "coordinates": [...], "curvature": 0.0 }
    """
    body: Dict[str, Any] = request.get_json(silent=True) or {}
    state: Optional[str] = body.get("state")
    if not state:
        return _bad_request("Missing required field: 'state'")

    nav = _get_navigator()
    result = nav.embed(str(state))
    return jsonify(result)


# ---------------------------------------------------------------------------
# Layer 2 — Geodesic
# ---------------------------------------------------------------------------

@sinax_bp.post("/geodesic")
def ptn_geodesic() -> Response:
    """
    Compute the geodesic flow between two proof states (Layer 2).

    Request body (JSON):
        {
          "start":    "<start proof state>",
          "target":   "<target proof state>",
          "max_time": 1.0          (optional, default 1.0)
        }

    Response:
        {
          "start_state": "...", "target_state": "...",
          "tactics": [...], "path_length": 0.0,
          "num_steps": N, "converged": true, "teleported": false
        }
    """
    body: Dict[str, Any] = request.get_json(silent=True) or {}
    start: Optional[str]  = body.get("start") or body.get("start_state")
    target: Optional[str] = body.get("target") or body.get("target_state")
    if not start or not target:
        return _bad_request("Missing required fields: 'start' and 'target'")

    max_time: float = float(body.get("max_time", 1.0))
    nav = _get_navigator()
    result = nav.geodesic(str(start), str(target), max_time=max_time)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Layer 3 — Homology
# ---------------------------------------------------------------------------

@sinax_bp.post("/homology")
def ptn_homology() -> Response:
    """
    Compute persistent homology over a set of proof states (Layer 3).

    Request body (JSON):
        { "states": ["⊢ P", "⊢ P ∧ Q", ...] }

    Response:
        {
          "num_states": N, "has_holes": true,
          "betti_numbers": [1, 0, ...],
          "num_components": 1,
          "hole_filling_suggestions": [...]
        }
    """
    body: Dict[str, Any] = request.get_json(silent=True) or {}
    states: Optional[List[str]] = (
        body.get("states") or body.get("proof_states")
    )
    if not states or not isinstance(states, list):
        return _bad_request("Missing required field: 'states' (array of proof state strings)")

    nav = _get_navigator()
    result = nav.homology([str(s) for s in states])
    return jsonify(result)


# ---------------------------------------------------------------------------
# Layer 4 — Morse
# ---------------------------------------------------------------------------

@sinax_bp.post("/morse")
def ptn_morse() -> Response:
    """
    Morse decomposition of a proof complexity landscape (Layer 4).

    Request body (JSON):
        { "states": ["⊢ base case", "⊢ inductive step", ...] }

    Response:
        {
          "key_lemmas": [...], "branch_points": [...],
          "min_proof_length_bound": N, "num_critical_points": M
        }
    """
    body: Dict[str, Any] = request.get_json(silent=True) or {}
    states: Optional[List[str]] = (
        body.get("states") or body.get("proof_states")
    )
    if not states or not isinstance(states, list):
        return _bad_request("Missing required field: 'states' (array of proof state strings)")

    nav = _get_navigator()
    result = nav.morse([str(s) for s in states])
    return jsonify(result)


# ---------------------------------------------------------------------------
# Full solve (all four layers)
# ---------------------------------------------------------------------------

@sinax_bp.post("/solve")
def ptn_solve() -> Response:
    """
    End-to-end proof search through all four PTN layers.

    Request body (JSON):
        {
          "start":   "<start proof state>",
          "target":  "<target proof state>",   (default: "closed")
          "context": ["<lemma 1>", ...]         (optional)
        }

    Response:
        Full ProofResult serialised to JSON, including tactic_sequence,
        proof_narrative, homology, Morse data, and performance telemetry.
    """
    body: Dict[str, Any] = request.get_json(silent=True) or {}
    start: Optional[str]  = body.get("start") or body.get("start_state")
    target: str           = str(body.get("target") or body.get("target_state") or "closed")
    context: Optional[List[str]] = body.get("context") or body.get("context_states")

    if not start:
        return _bad_request("Missing required field: 'start'")

    nav = _get_navigator()
    proof = nav.solve(
        start_state=str(start),
        target_state=target,
        context_states=[str(s) for s in context] if context else None,
    )
    return jsonify(proof.to_dict())
