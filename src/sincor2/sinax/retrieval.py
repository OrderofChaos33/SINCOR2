"""
SINAX — Phase 3: Embedding Retrieval
======================================
Provides a retrieval service that indexes all embedded nodes in the
``ProofGraphStore`` and answers k-NN queries efficiently.

Design
------
* Backed by the existing ``ProofGraphStore.nearest_neighbours`` which
  does an exact linear scan (fine for ≤ 50 k nodes with 256-d vectors).
* Exposes a higher-level ``RetrievalService`` with caching, filtering,
  and async-friendly helpers.
* A pluggable ``EmbeddingIndex`` ABC allows drop-in replacement with an
  approximate NN index (e.g. FAISS, HNSWlib) without changing call-sites.

Public API
----------
    EmbeddingIndex      — ABC for pluggable ANN backends
    LinearScanIndex     — default exact linear-scan index
    RetrievalService    — high-level retrieval with query caching
    get_retrieval_service()  — singleton accessor
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from typing import List, Optional, Sequence, Tuple

import numpy as np

from . import config as cfg
from .encoder import Embedding, ProofState, get_encoder
from .graph_store import ProofStateNode, ProofGraphStore, get_store

logger = logging.getLogger("sincor.sinax.retrieval")

# ---------------------------------------------------------------------------
# Pluggable index ABC
# ---------------------------------------------------------------------------


class EmbeddingIndex(ABC):
    """Abstract base class for embedding-based NN backends."""

    @abstractmethod
    def index(self, nodes: Sequence[ProofStateNode]) -> None:
        """(Re-)build the index from ``nodes``."""

    @abstractmethod
    def query(
        self, query: Embedding, k: int
    ) -> List[Tuple[ProofStateNode, float]]:
        """Return the ``k`` nearest nodes with their similarity scores."""

    @abstractmethod
    def add(self, node: ProofStateNode) -> None:
        """Incrementally add a single node to the index."""


# ---------------------------------------------------------------------------
# Linear-scan index (default, no extra deps)
# ---------------------------------------------------------------------------


class LinearScanIndex(EmbeddingIndex):
    """Exact cosine-similarity index using a numpy matrix (linear scan).

    Suitable for up to ~50 k nodes with dim ≤ 1024.  For larger graphs
    replace with a FAISS-backed implementation.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._nodes: List[ProofStateNode] = []
        # Row-normalised matrix of shape (N, D); built lazily
        self._matrix: Optional[np.ndarray] = None
        self._dirty: bool = True

    def index(self, nodes: Sequence[ProofStateNode]) -> None:
        with self._lock:
            self._nodes = [n for n in nodes if n.embedding is not None]
            self._dirty = True

    def add(self, node: ProofStateNode) -> None:
        if node.embedding is None:
            return
        with self._lock:
            self._nodes.append(node)
            self._dirty = True

    def query(
        self, query: Embedding, k: int
    ) -> List[Tuple[ProofStateNode, float]]:
        with self._lock:
            if not self._nodes:
                return []
            if self._dirty:
                self._rebuild()
            q = query.vector.astype(np.float64)
            q_norm = np.linalg.norm(q) or 1.0
            q = q / q_norm  # (D,)
            sims = self._matrix @ q  # (N,)
            k_actual = min(k, len(self._nodes))
            top_k_idx = np.argpartition(sims, -k_actual)[-k_actual:]
            top_k_idx = top_k_idx[np.argsort(-sims[top_k_idx])]
            return [(self._nodes[i], float(sims[i])) for i in top_k_idx]

    def _rebuild(self) -> None:
        """Build the row-normalised matrix from self._nodes."""
        dim = self._nodes[0].embedding.dim if self._nodes else 0
        mat = np.stack(
            [n.embedding.vector.astype(np.float64) for n in self._nodes]
        )  # (N, D)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._matrix = mat / norms
        self._dirty = False


# ---------------------------------------------------------------------------
# Retrieval Service
# ---------------------------------------------------------------------------


class RetrievalService:
    """High-level embedding retrieval service.

    Wraps an ``EmbeddingIndex`` and adds:
    * Automatic query encoding when a raw ``ProofState`` is supplied.
    * LRU-style query cache (keyed on ``proof_state_hash``).
    * Incremental indexing: call ``add_node`` after each new graph node.
    """

    _CACHE_MAX = 512

    def __init__(
        self,
        store: Optional[ProofGraphStore] = None,
        index: Optional[EmbeddingIndex] = None,
    ) -> None:
        self._store = store or get_store()
        self._index: EmbeddingIndex = index or LinearScanIndex()
        self._lock = threading.Lock()
        self._cache: dict = {}
        self._cache_order: list = []  # insertion-order list of cache keys

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def rebuild_index(self) -> None:
        """Rebuild the full index from the current graph store."""
        nodes = list(self._store._nodes.values())
        self._index.index(nodes)
        logger.info("RetrievalService: index rebuilt with %d nodes", len(nodes))

    def add_node(self, node: ProofStateNode) -> None:
        """Incrementally add a node to the index."""
        self._index.add(node)
        logger.debug("RetrievalService: node %s added to index", node.node_id)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(
        self,
        query: "ProofState | Embedding",
        k: Optional[int] = None,
        use_cache: bool = True,
    ) -> List[Tuple[ProofStateNode, float]]:
        """Find the k nodes most similar to ``query``.

        Parameters
        ----------
        query:
            Either a ``ProofState`` (will be encoded on the fly) or a
            pre-computed ``Embedding``.
        k:
            Number of neighbours.  Defaults to ``cfg.KNN_K``.
        use_cache:
            When ``True``, cache results keyed on the embedding's
            ``proof_state_hash``.

        Returns
        -------
        List of (node, similarity) pairs, sorted descending by similarity.
        """
        k = k or cfg.KNN_K
        emb: Embedding

        if isinstance(query, Embedding):
            emb = query
        else:
            encoder = get_encoder()
            emb = encoder.encode(query)

        if use_cache:
            cached = self._cache.get(emb.proof_state_hash)
            if cached is not None:
                return cached

        results = self._index.query(emb, k=k)

        if use_cache:
            self._cache_put(emb.proof_state_hash, results)

        return results

    # ------------------------------------------------------------------
    # Internal cache helpers
    # ------------------------------------------------------------------

    def _cache_put(self, key: str, value: list) -> None:
        with self._lock:
            if key in self._cache:
                return
            if len(self._cache_order) >= self._CACHE_MAX:
                oldest = self._cache_order.pop(0)
                self._cache.pop(oldest, None)
            self._cache[key] = value
            self._cache_order.append(key)

    def invalidate_cache(self) -> None:
        """Clear the query cache (call after significant graph updates)."""
        with self._lock:
            self._cache.clear()
            self._cache_order.clear()

    def stats(self) -> dict:
        return {
            "index_size": len(getattr(self._index, "_nodes", [])),
            "cache_size": len(self._cache),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_service_instance: Optional[RetrievalService] = None
_service_lock = threading.Lock()


def get_retrieval_service() -> RetrievalService:
    """Return the process-global ``RetrievalService`` singleton."""
    global _service_instance
    if _service_instance is None:
        with _service_lock:
            if _service_instance is None:
                _service_instance = RetrievalService()
    return _service_instance
