"""
SINAX — Phase 7 (part 2): Visualization Layer
================================================
Provides lightweight visualization helpers for the proof graph, curvature
hotspots, tactic trajectories, and discovered lemmas.

All outputs are JSON-serialisable so they can be consumed by any front-end
(D3.js, vis.js, Plotly, etc.) or returned directly from a Flask route.

Public API
----------
    GraphVisualizer     — converts subgraph to a node/edge JSON payload
    TrajectoryVisualizer— highlights a trajectory on the graph
    CurvatureHeatmap    — builds a heatmap payload for curvature scores
    LemmaReport         — summary report of discovered lemmas
    SINAXDashboard      — aggregates all the above into one response dict
    get_dashboard()     — singleton accessor
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from . import config as cfg
from .curvature import CurvatureAnalyzer, RegionCurvature, get_curvature_analyzer
from .graph_store import ProofGraphStore, TacticEdge, VerificationResult, get_store
from .lemma_discovery import LemmaCandidate, LemmaDiscoveryEngine, get_lemma_engine

logger = logging.getLogger("sincor.sinax.visualization")

# ---------------------------------------------------------------------------
# Graph Visualizer
# ---------------------------------------------------------------------------


class GraphVisualizer:
    """Serialize a subgraph (BFS from a root) as a JSON-ready node/edge dict.

    Response format
    ---------------
    ::

        {
          "nodes": [
            {"id": "...", "label": "...", "depth": 0, "metadata": {...}}
          ],
          "edges": [
            {"source": "...", "target": "...", "tactic": "...", "verified": true,
             "cost": 0.0}
          ],
          "stats": {"nodes": N, "edges": M}
        }
    """

    def __init__(self, store: Optional[ProofGraphStore] = None) -> None:
        self._store = store or get_store()

    def render(
        self,
        root_id: str,
        max_depth: int = 5,
        max_nodes: int = 200,
    ) -> Dict[str, Any]:
        """Build the JSON payload for the subgraph rooted at ``root_id``."""
        nodes_in_view = self._store.bfs(root_id, max_depth=max_depth)[:max_nodes]
        node_ids = {n.node_id for n in nodes_in_view}

        node_list = []
        for n in nodes_in_view:
            label = n.proof_state.goal[:60] + ("…" if len(n.proof_state.goal) > 60 else "")
            node_list.append(
                {
                    "id": n.node_id,
                    "label": label,
                    "depth": n.depth,
                    "metadata": n.metadata,
                }
            )

        edge_list = []
        seen_edges = set()
        for n in nodes_in_view:
            for target, edge in self._store.successors(n.node_id):
                if edge.edge_id in seen_edges:
                    continue
                if target.node_id not in node_ids:
                    continue
                seen_edges.add(edge.edge_id)
                edge_list.append(
                    {
                        "id": edge.edge_id,
                        "source": edge.source_id,
                        "target": edge.target_id,
                        "tactic": edge.tactic,
                        "verified": edge.verified,
                        "cost": edge.cost,
                    }
                )

        return {
            "nodes": node_list,
            "edges": edge_list,
            "stats": {"nodes": len(node_list), "edges": len(edge_list)},
        }

    def render_full(self, max_nodes: int = 500) -> Dict[str, Any]:
        """Render the entire store (up to ``max_nodes`` nodes by insertion order)."""
        with self._store._lock:
            all_nodes = list(self._store._nodes.values())[:max_nodes]
            node_ids = {n.node_id for n in all_nodes}
            node_list = [
                {
                    "id": n.node_id,
                    "label": n.proof_state.goal[:60],
                    "depth": n.depth,
                    "metadata": n.metadata,
                }
                for n in all_nodes
            ]
            edge_list = []
            seen_edges: set = set()
            for n in all_nodes:
                for target, edge in self._store.successors(n.node_id):
                    if edge.edge_id in seen_edges or target.node_id not in node_ids:
                        continue
                    seen_edges.add(edge.edge_id)
                    edge_list.append(
                        {
                            "id": edge.edge_id,
                            "source": edge.source_id,
                            "target": edge.target_id,
                            "tactic": edge.tactic,
                            "verified": edge.verified,
                            "cost": edge.cost,
                        }
                    )
        return {
            "nodes": node_list,
            "edges": edge_list,
            "stats": {
                "nodes": len(node_list),
                "edges": len(edge_list),
                "total_store_nodes": self._store.stats()["nodes"],
            },
        }


# ---------------------------------------------------------------------------
# Trajectory Visualizer
# ---------------------------------------------------------------------------


class TrajectoryVisualizer:
    """Highlight a reconstructed trajectory on the graph."""

    def __init__(self, store: Optional[ProofGraphStore] = None) -> None:
        self._store = store or get_store()

    def render(self, start_id: str, end_id: str) -> Dict[str, Any]:
        """Return a trajectory payload for the path from ``start_id`` to ``end_id``."""
        path = self._store.reconstruct_path(start_id, end_id)
        if path is None:
            return {"found": False, "steps": []}

        steps = []
        for node, edge in path:
            steps.append(
                {
                    "node_id": node.node_id,
                    "goal": node.proof_state.goal[:80],
                    "depth": node.depth,
                    "tactic": edge.tactic if edge else None,
                    "verified": edge.verified if edge else None,
                    "cost": edge.cost if edge else 0.0,
                }
            )
        total_cost = sum(s["cost"] or 0.0 for s in steps)
        return {
            "found": True,
            "start_id": start_id,
            "end_id": end_id,
            "length": len(steps),
            "total_cost": total_cost,
            "steps": steps,
        }


# ---------------------------------------------------------------------------
# Curvature Heatmap
# ---------------------------------------------------------------------------


class CurvatureHeatmap:
    """Build a heatmap payload of curvature scores across the graph."""

    def __init__(
        self,
        store: Optional[ProofGraphStore] = None,
        analyzer: Optional[CurvatureAnalyzer] = None,
    ) -> None:
        self._store = store or get_store()
        self._analyzer = analyzer or get_curvature_analyzer()

    def render(self, top_k: int = 50, radius: int = 2) -> Dict[str, Any]:
        """Return heatmap data: list of {node_id, goal, curvature_score}."""
        hotspots = self._analyzer.top_curvature_nodes(top_k=top_k, radius=radius)
        entries = []
        for node_id, score in hotspots:
            node = self._store.get_node(node_id)
            entries.append(
                {
                    "node_id": node_id,
                    "goal": node.proof_state.goal[:60] if node else "",
                    "depth": node.depth if node else 0,
                    "curvature_score": round(score, 4),
                }
            )
        return {"heatmap": entries, "count": len(entries)}


# ---------------------------------------------------------------------------
# Lemma Report
# ---------------------------------------------------------------------------


class LemmaReport:
    """Summary report of all discovered lemma candidates."""

    def __init__(self, engine: Optional[LemmaDiscoveryEngine] = None) -> None:
        self._engine = engine or get_lemma_engine()

    def render(self) -> Dict[str, Any]:
        """Return a ranked list of discovered lemma candidates."""
        candidates = self._engine.get_all_candidates()
        return {
            "lemmas": [c.to_dict() for c in candidates],
            "count": len(candidates),
            "verified_count": sum(
                1
                for c in candidates
                if c.verification_result == VerificationResult.VERIFIED
            ),
            "pending_count": sum(
                1
                for c in candidates
                if c.verification_result == VerificationResult.PENDING
            ),
        }


# ---------------------------------------------------------------------------
# SINAX Dashboard
# ---------------------------------------------------------------------------


class SINAXDashboard:
    """Aggregates all SINAX visualization components into a single response."""

    def __init__(
        self,
        store: Optional[ProofGraphStore] = None,
        analyzer: Optional[CurvatureAnalyzer] = None,
        lemma_engine: Optional[LemmaDiscoveryEngine] = None,
    ) -> None:
        self._store = store or get_store()
        self._graph_viz = GraphVisualizer(self._store)
        self._traj_viz = TrajectoryVisualizer(self._store)
        self._heatmap = CurvatureHeatmap(self._store, analyzer)
        self._lemma_report = LemmaReport(lemma_engine)

    def render_summary(self) -> Dict[str, Any]:
        """Return a high-level summary without heavy graph serialization."""
        store_stats = self._store.stats()
        heatmap = self._heatmap.render(top_k=10)
        lemmas = self._lemma_report.render()
        return {
            "sinax_enabled": cfg.SINAX_ENABLED,
            "mode": cfg.SINAX_MODE,
            "graph": store_stats,
            "hotspots": heatmap["heatmap"][:5],
            "lemmas": {
                "total": lemmas["count"],
                "verified": lemmas["verified_count"],
                "pending": lemmas["pending_count"],
            },
        }

    def render_full_graph(self, max_nodes: int = 500) -> Dict[str, Any]:
        return self._graph_viz.render_full(max_nodes=max_nodes)

    def render_trajectory(self, start_id: str, end_id: str) -> Dict[str, Any]:
        return self._traj_viz.render(start_id, end_id)

    def render_heatmap(self, top_k: int = 50) -> Dict[str, Any]:
        return self._heatmap.render(top_k=top_k)

    def render_lemma_report(self) -> Dict[str, Any]:
        return self._lemma_report.render()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_dashboard_instance: Optional[SINAXDashboard] = None
_dashboard_lock = __import__("threading").Lock()


def get_dashboard() -> SINAXDashboard:
    """Return the process-global ``SINAXDashboard`` singleton."""
    global _dashboard_instance
    if _dashboard_instance is None:
        import threading

        with _dashboard_lock:
            if _dashboard_instance is None:
                _dashboard_instance = SINAXDashboard()
    return _dashboard_instance
