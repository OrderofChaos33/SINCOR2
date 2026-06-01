"""
SINAX — Phase 2: Proof Graph Store
====================================
Stores verified proof transitions as a directed graph.

Nodes
-----
    ProofStateNode  — proof state + embedding + metadata

Edges
-----
    TacticEdge      — tactic applied, verifier result, cost, timestamp

Invariant
---------
    When ``cfg.GRAPH_WRITE_ONLY_VERIFIED`` is ``True`` (the default), edges
    can only be added when their ``verified`` field is ``True``.  This
    ensures the graph only encodes proof moves that have been certified by
    the active verifier (Lean or another backend).

Public API
----------
    VerificationResult  — enum: PENDING / VERIFIED / FAILED
    ProofStateNode      — node value-object
    TacticEdge          — edge value-object
    ProofGraphStore     — main graph container
    get_store()         — singleton accessor
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

from . import config as cfg
from .encoder import Embedding, ProofState

logger = logging.getLogger("sincor.sinax.graph_store")

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


class VerificationResult(str, Enum):
    """Result returned by the active proof verifier."""

    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"


@dataclass
class ProofStateNode:
    """A node in the proof graph.

    Attributes
    ----------
    node_id:
        UUID-style identifier for this node.
    proof_state:
        The canonical ``ProofState`` at this node.
    embedding:
        Dense vector for this node; ``None`` if not yet encoded.
    depth:
        Number of tactic steps from the root/initial state.
    metadata:
        Arbitrary key-value pairs (theorem name, source file, …).
    created_at:
        Unix timestamp (float) when the node was created.
    """

    node_id: str
    proof_state: ProofState
    embedding: Optional[Embedding] = None
    depth: int = 0
    metadata: Dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.embedding is not None:
            d["embedding"] = {
                "vector": self.embedding.vector.tolist(),
                "model_version": self.embedding.model_version,
                "proof_state_hash": self.embedding.proof_state_hash,
                "dim": self.embedding.dim,
            }
        return d


@dataclass
class TacticEdge:
    """A directed edge representing a single tactic application.

    Attributes
    ----------
    edge_id:
        UUID-style identifier.
    source_id:
        ``node_id`` of the source ``ProofStateNode``.
    target_id:
        ``node_id`` of the target ``ProofStateNode``.
    tactic:
        The tactic string that was applied.
    verification_result:
        Result from the active verifier.
    cost:
        Numeric cost (e.g. token usage, wall-clock time) of this step.
    timestamp:
        Unix timestamp when this edge was recorded.
    metadata:
        Arbitrary extra information.
    """

    edge_id: str
    source_id: str
    target_id: str
    tactic: str
    verification_result: VerificationResult
    cost: float = 0.0
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, str] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        return self.verification_result == VerificationResult.VERIFIED

    def to_dict(self) -> dict:
        d = asdict(self)
        d["verification_result"] = self.verification_result.value
        return d


# ---------------------------------------------------------------------------
# Graph Store
# ---------------------------------------------------------------------------


class ProofGraphStore:
    """In-memory directed graph of verified proof transitions.

    Thread-safety
    -------------
    All public mutating methods are protected by a ``threading.RLock``.
    Read-only methods (lookups, traversals) acquire a read-compatible
    lock via the same ``RLock`` to avoid torn reads.

    LRU eviction
    ------------
    When ``len(nodes) >= cfg.GRAPH_MAX_NODES``, the oldest node (by
    ``created_at``) and all its edges are evicted before inserting the
    new node.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # node_id → ProofStateNode
        self._nodes: Dict[str, ProofStateNode] = {}
        # edge_id → TacticEdge
        self._edges: Dict[str, TacticEdge] = {}
        # adjacency: source_id → {target_id: [TacticEdge]}
        self._out_edges: Dict[str, Dict[str, List[TacticEdge]]] = defaultdict(
            lambda: defaultdict(list)
        )
        # reverse adjacency: target_id → {source_id: [TacticEdge]}
        self._in_edges: Dict[str, Dict[str, List[TacticEdge]]] = defaultdict(
            lambda: defaultdict(list)
        )
        # FIFO insertion order for LRU eviction
        self._insertion_order: deque[str] = deque()

        logger.debug("ProofGraphStore initialised (max_nodes=%d)", cfg.GRAPH_MAX_NODES)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add_node(self, node: ProofStateNode) -> None:
        """Add a node to the graph, evicting the oldest if capacity is reached."""
        with self._lock:
            if node.node_id in self._nodes:
                logger.debug("Node %s already exists; skipping", node.node_id)
                return
            self._evict_if_needed()
            self._nodes[node.node_id] = node
            self._insertion_order.append(node.node_id)
            logger.debug("Added node %s (depth=%d)", node.node_id, node.depth)

    def add_edge(self, edge: TacticEdge) -> bool:
        """Add an edge to the graph.

        Returns
        -------
        bool
            ``True`` if the edge was added, ``False`` if rejected (e.g.
            because ``GRAPH_WRITE_ONLY_VERIFIED`` is set and the edge is
            not verified, or because either endpoint is missing).
        """
        with self._lock:
            if cfg.GRAPH_WRITE_ONLY_VERIFIED and not edge.verified:
                logger.debug(
                    "Edge %s rejected: not verified (GRAPH_WRITE_ONLY_VERIFIED=True)",
                    edge.edge_id,
                )
                return False
            if edge.source_id not in self._nodes:
                logger.warning(
                    "Edge %s rejected: source node %s not in graph",
                    edge.edge_id, edge.source_id,
                )
                return False
            if edge.target_id not in self._nodes:
                logger.warning(
                    "Edge %s rejected: target node %s not in graph",
                    edge.edge_id, edge.target_id,
                )
                return False
            if edge.edge_id in self._edges:
                return True  # idempotent
            self._edges[edge.edge_id] = edge
            self._out_edges[edge.source_id][edge.target_id].append(edge)
            self._in_edges[edge.target_id][edge.source_id].append(edge)
            return True

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> Optional[ProofStateNode]:
        with self._lock:
            return self._nodes.get(node_id)

    def get_edge(self, edge_id: str) -> Optional[TacticEdge]:
        with self._lock:
            return self._edges.get(edge_id)

    def successors(self, node_id: str) -> List[Tuple[ProofStateNode, TacticEdge]]:
        """Return all (target_node, edge) pairs reachable from ``node_id``."""
        with self._lock:
            result = []
            for target_id, edges in self._out_edges.get(node_id, {}).items():
                target = self._nodes.get(target_id)
                if target is not None:
                    for edge in edges:
                        result.append((target, edge))
            return result

    def predecessors(self, node_id: str) -> List[Tuple[ProofStateNode, TacticEdge]]:
        """Return all (source_node, edge) pairs leading to ``node_id``."""
        with self._lock:
            result = []
            for src_id, edges in self._in_edges.get(node_id, {}).items():
                src = self._nodes.get(src_id)
                if src is not None:
                    for edge in edges:
                        result.append((src, edge))
            return result

    # ------------------------------------------------------------------
    # Nearest-neighbour lookup
    # ------------------------------------------------------------------

    def nearest_neighbours(
        self,
        query_embedding: Embedding,
        k: int = 16,
        require_same_version: bool = True,
    ) -> List[Tuple[ProofStateNode, float]]:
        """Return the k nodes with highest cosine similarity to ``query_embedding``.

        Parameters
        ----------
        query_embedding:
            The query embedding.
        k:
            Number of neighbours to return.
        require_same_version:
            When ``True``, skip nodes whose embedding model version differs
            from ``query_embedding.model_version``.

        Returns
        -------
        List of (node, similarity) pairs, sorted descending by similarity.
        """
        with self._lock:
            q = query_embedding.vector.astype(np.float64)
            q_norm = np.linalg.norm(q) or 1.0
            q = q / q_norm

            candidates: List[Tuple[float, ProofStateNode]] = []
            for node in self._nodes.values():
                if node.embedding is None:
                    continue
                if require_same_version and (
                    node.embedding.model_version != query_embedding.model_version
                ):
                    continue
                v = node.embedding.vector.astype(np.float64)
                v_norm = np.linalg.norm(v) or 1.0
                sim = float(np.dot(q, v / v_norm))
                candidates.append((sim, node))

            candidates.sort(key=lambda x: x[0], reverse=True)
            return [(node, sim) for sim, node in candidates[:k]]

    # ------------------------------------------------------------------
    # Path reconstruction (BFS)
    # ------------------------------------------------------------------

    def reconstruct_path(
        self, start_id: str, end_id: str
    ) -> Optional[List[Tuple[ProofStateNode, Optional[TacticEdge]]]]:
        """BFS shortest-path from ``start_id`` to ``end_id``.

        Returns
        -------
        List of (node, edge_to_next) pairs if a path exists, else ``None``.
        The last element has ``edge_to_next = None``.
        """
        with self._lock:
            if start_id not in self._nodes or end_id not in self._nodes:
                return None
            if start_id == end_id:
                return [(self._nodes[start_id], None)]

            # BFS
            visited: Set[str] = {start_id}
            # queue: (current_id, path_so_far)
            queue: deque[Tuple[str, List[Tuple[str, Optional[str]]]]] = deque(
                [(start_id, [(start_id, None)])]
            )
            while queue:
                curr_id, path = queue.popleft()
                for target_id, edges in self._out_edges.get(curr_id, {}).items():
                    if target_id in visited:
                        continue
                    edge = edges[0]  # pick first (lowest cost heuristic)
                    new_path = path + [(target_id, edge.edge_id)]
                    if target_id == end_id:
                        # Materialise
                        result = []
                        for nid, eid in new_path[:-1]:
                            result.append((self._nodes[nid], self._edges[eid]))
                        result.append((self._nodes[end_id], None))
                        return result
                    visited.add(target_id)
                    queue.append((target_id, new_path))
            return None

    # ------------------------------------------------------------------
    # Graph traversal (BFS from a root)
    # ------------------------------------------------------------------

    def bfs(self, root_id: str, max_depth: int = 10) -> List[ProofStateNode]:
        """BFS traversal returning nodes in visit order up to ``max_depth``."""
        with self._lock:
            if root_id not in self._nodes:
                return []
            visited: Set[str] = set()
            queue: deque[Tuple[str, int]] = deque([(root_id, 0)])
            result: List[ProofStateNode] = []
            while queue:
                nid, depth = queue.popleft()
                if nid in visited or depth > max_depth:
                    continue
                visited.add(nid)
                result.append(self._nodes[nid])
                for target_id in self._out_edges.get(nid, {}):
                    if target_id not in visited:
                        queue.append((target_id, depth + 1))
            return result

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        with self._lock:
            return {
                "nodes": len(self._nodes),
                "edges": len(self._edges),
                "max_nodes": cfg.GRAPH_MAX_NODES,
            }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evict_if_needed(self) -> None:
        """Remove the oldest node when the graph is at capacity."""
        while len(self._nodes) >= cfg.GRAPH_MAX_NODES:
            oldest_id = self._insertion_order.popleft()
            if oldest_id not in self._nodes:
                continue
            self._evict_node(oldest_id)

    def _evict_node(self, node_id: str) -> None:
        del self._nodes[node_id]
        # Remove all outgoing edges
        for target_id, edges in list(self._out_edges.pop(node_id, {}).items()):
            for edge in edges:
                self._edges.pop(edge.edge_id, None)
                self._in_edges.get(target_id, {}).pop(node_id, None)
        # Remove all incoming edges
        for src_id, edges in list(self._in_edges.pop(node_id, {}).items()):
            for edge in edges:
                self._edges.pop(edge.edge_id, None)
                self._out_edges.get(src_id, {}).pop(node_id, None)
        logger.debug("Evicted node %s (LRU)", node_id)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_store_instance: Optional[ProofGraphStore] = None
_store_lock = threading.Lock()


def get_store() -> ProofGraphStore:
    """Return the process-global ``ProofGraphStore`` singleton."""
    global _store_instance
    if _store_instance is None:
        with _store_lock:
            if _store_instance is None:
                _store_instance = ProofGraphStore()
    return _store_instance
