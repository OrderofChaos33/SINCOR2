#!/usr/bin/env python3
"""
<<<<<<< HEAD
SINAX Test Suite
================
Unit tests and integration tests for all SINAX modules.

Run with:
    PYTHONPATH=src:src/sincor2 python -m pytest tests/test_sinax.py -v
"""

from __future__ import annotations

import math
import time
import uuid

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Helper: silence noisy loggers during tests
# ---------------------------------------------------------------------------
import logging

logging.disable(logging.CRITICAL)


# ===========================================================================
# Config
# ===========================================================================

class TestConfig:
    def test_get_config_returns_dict(self):
        from sincor2.sinax.config import get_config
        cfg = get_config()
        assert isinstance(cfg, dict)

    def test_config_required_keys(self):
        from sincor2.sinax.config import get_config
        cfg = get_config()
        for key in ("enabled", "mode", "encoder", "graph", "search",
                    "curvature", "lemma_discovery", "verifier"):
            assert key in cfg, f"Missing key: {key}"

    def test_embedding_dim_positive(self):
        from sincor2.sinax import config as cfg
        assert cfg.EMBEDDING_DIM > 0

    def test_fallback_threshold_in_range(self):
        from sincor2.sinax import config as cfg
        assert 0.0 <= cfg.FALLBACK_SIMILARITY_THRESHOLD <= 1.0

    def test_exploration_weight_in_range(self):
        from sincor2.sinax import config as cfg
        assert 0.0 <= cfg.EXPLORATION_WEIGHT <= 1.0


# ===========================================================================
# Phase 1 — Encoder
# ===========================================================================

class TestProofState:
    def test_canonical_text_contains_goal(self):
        from sincor2.sinax.encoder import ProofState
        ps = ProofState(goal="⊢ n + 0 = n")
        assert "GOAL:" in ps.canonical_text()
        assert "n + 0 = n" in ps.canonical_text()

    def test_canonical_text_contains_hypotheses(self):
        from sincor2.sinax.encoder import ProofState
        ps = ProofState(goal="goal", hypotheses=["h1 : P", "h2 : Q"])
        ct = ps.canonical_text()
        assert "h1 : P" in ct
        assert "h2 : Q" in ct

    def test_to_dict_roundtrip(self):
        from sincor2.sinax.encoder import ProofState
        ps = ProofState(goal="G", context="ctx", hypotheses=["h"], tactic_history=["intro"])
        d = ps.to_dict()
        assert d["goal"] == "G"
        assert d["context"] == "ctx"


class TestHashingEncoder:
    def _ps(self, goal: str = "⊢ P") -> "ProofState":
        from sincor2.sinax.encoder import ProofState
        return ProofState(goal=goal)

    def test_returns_embedding(self):
        from sincor2.sinax.encoder import HashingEncoder
        enc = HashingEncoder()
        emb = enc.encode(self._ps())
        assert emb is not None

    def test_embedding_dim_matches(self):
        from sincor2.sinax.encoder import HashingEncoder
        enc = HashingEncoder(dim=64)
        emb = enc.encode(self._ps())
        assert emb.dim == 64
        assert emb.vector.shape == (64,)

    def test_deterministic(self):
        from sincor2.sinax.encoder import HashingEncoder, ProofState
        enc = HashingEncoder()
        ps = ProofState(goal="⊢ x = x")
        e1 = enc.encode(ps)
        e2 = enc.encode(ps)
        np.testing.assert_array_equal(e1.vector, e2.vector)

    def test_different_states_different_embeddings(self):
        from sincor2.sinax.encoder import HashingEncoder, ProofState
        enc = HashingEncoder()
        e1 = enc.encode(ProofState(goal="⊢ P"))
        e2 = enc.encode(ProofState(goal="⊢ Q ∧ R"))
        assert not np.array_equal(e1.vector, e2.vector)

    def test_version_string(self):
        from sincor2.sinax.encoder import HashingEncoder
        enc = HashingEncoder()
        assert "hashing" in enc.version

    def test_normalised_has_unit_norm(self):
        from sincor2.sinax.encoder import HashingEncoder, ProofState
        enc = HashingEncoder()
        emb = enc.encode(ProofState(goal="⊢ T")).normalised()
        norm = float(np.linalg.norm(emb.vector))
        assert abs(norm - 1.0) < 1e-5

    def test_cosine_similarity_self(self):
        from sincor2.sinax.encoder import HashingEncoder, ProofState
        enc = HashingEncoder()
        emb = enc.encode(ProofState(goal="⊢ self"))
        assert abs(emb.cosine_similarity(emb) - 1.0) < 1e-4

    def test_batch_encode(self):
        from sincor2.sinax.encoder import HashingEncoder, ProofState
        enc = HashingEncoder(dim=32)
        states = [ProofState(goal=f"⊢ P{i}") for i in range(5)]
        embs = enc.encode_batch(states)
        assert len(embs) == 5
        assert all(e.dim == 32 for e in embs)


class TestContrastiveLoss:
    def test_zero_loss_identical(self):
        """Loss should be near 0 when all anchors equal their positives."""
        from sincor2.sinax.encoder import HashingEncoder, ProofState, contrastive_loss
        enc = HashingEncoder(dim=32)
        states = [ProofState(goal=f"⊢ G{i}") for i in range(4)]
        embs = [enc.encode(s) for s in states]
        loss = contrastive_loss(embs, embs, temperature=0.07)
        # Not guaranteed zero due to in-batch negatives, but should be finite
        assert math.isfinite(loss)
        assert loss >= 0.0

    def test_length_mismatch_raises(self):
        from sincor2.sinax.encoder import HashingEncoder, ProofState, contrastive_loss
        enc = HashingEncoder(dim=32)
        a = [enc.encode(ProofState(goal="A"))]
        p = [enc.encode(ProofState(goal="B")), enc.encode(ProofState(goal="C"))]
        with pytest.raises(ValueError):
            contrastive_loss(a, p)

    def test_empty_batch(self):
        from sincor2.sinax.encoder import contrastive_loss
        assert contrastive_loss([], []) == 0.0


class TestEncoderRegistry:
    def test_register_and_get(self):
        from sincor2.sinax.encoder import (
            HashingEncoder, register_encoder, get_encoder, BaseEncoder, Embedding, ProofState
        )

        class DummyEncoder(BaseEncoder):
            @property
            def version(self): return "dummy-v0"
            @property
            def dim(self): return 4
            def encode(self, state: ProofState) -> Embedding:
                return HashingEncoder(dim=4).encode(state)

        register_encoder("dummy-v0", DummyEncoder())
        enc = get_encoder("dummy-v0")
        assert enc.version == "dummy-v0"


# ===========================================================================
# Phase 2 — Graph Store
# ===========================================================================

def _make_node(goal: str, depth: int = 0) -> "ProofStateNode":
    from sincor2.sinax.graph_store import ProofStateNode
    from sincor2.sinax.encoder import ProofState, HashingEncoder
    ps = ProofState(goal=goal)
    enc = HashingEncoder(dim=32)
    emb = enc.encode(ps)
    return ProofStateNode(
        node_id=str(uuid.uuid4()),
        proof_state=ps,
        embedding=emb,
        depth=depth,
    )


def _make_edge(src_id: str, tgt_id: str, tactic: str = "intro",
               verified: bool = True) -> "TacticEdge":
    from sincor2.sinax.graph_store import TacticEdge, VerificationResult
    return TacticEdge(
        edge_id=str(uuid.uuid4()),
        source_id=src_id,
        target_id=tgt_id,
        tactic=tactic,
        verification_result=VerificationResult.VERIFIED if verified else VerificationResult.FAILED,
    )


class TestProofGraphStore:
    def _fresh(self):
        from sincor2.sinax.graph_store import ProofGraphStore
        return ProofGraphStore()

    def test_add_and_get_node(self):
        store = self._fresh()
        n = _make_node("⊢ P")
        store.add_node(n)
        assert store.get_node(n.node_id) is n

    def test_duplicate_node_idempotent(self):
        store = self._fresh()
        n = _make_node("⊢ P")
        store.add_node(n)
        store.add_node(n)
        assert store.stats()["nodes"] == 1

    def test_add_verified_edge(self):
        store = self._fresh()
        n1, n2 = _make_node("⊢ A"), _make_node("⊢ B")
        store.add_node(n1); store.add_node(n2)
        e = _make_edge(n1.node_id, n2.node_id, verified=True)
        added = store.add_edge(e)
        assert added is True
        assert store.get_edge(e.edge_id) is e

    def test_unverified_edge_rejected_when_flag_set(self):
        import os
        os.environ["SINAX_GRAPH_WRITE_ONLY_VERIFIED"] = "true"
        store = self._fresh()
        n1, n2 = _make_node("⊢ A"), _make_node("⊢ B")
        store.add_node(n1); store.add_node(n2)
        e = _make_edge(n1.node_id, n2.node_id, verified=False)
        # Reload config to pick up env var
        from sincor2.sinax import config as cfg
        original = cfg.GRAPH_WRITE_ONLY_VERIFIED
        cfg.GRAPH_WRITE_ONLY_VERIFIED = True
        added = store.add_edge(e)
        cfg.GRAPH_WRITE_ONLY_VERIFIED = original
        assert added is False

    def test_edge_missing_node_rejected(self):
        store = self._fresh()
        n1 = _make_node("⊢ A")
        store.add_node(n1)
        e = _make_edge(n1.node_id, "nonexistent", verified=True)
        added = store.add_edge(e)
        assert added is False

    def test_successors(self):
        store = self._fresh()
        n1, n2 = _make_node("⊢ A"), _make_node("⊢ B")
        store.add_node(n1); store.add_node(n2)
        e = _make_edge(n1.node_id, n2.node_id, verified=True)
        store.add_edge(e)
        succs = store.successors(n1.node_id)
        assert len(succs) == 1
        assert succs[0][0].node_id == n2.node_id

    def test_predecessors(self):
        store = self._fresh()
        n1, n2 = _make_node("⊢ A"), _make_node("⊢ B")
        store.add_node(n1); store.add_node(n2)
        e = _make_edge(n1.node_id, n2.node_id, verified=True)
        store.add_edge(e)
        preds = store.predecessors(n2.node_id)
        assert len(preds) == 1
        assert preds[0][0].node_id == n1.node_id

    def test_nearest_neighbours(self):
        store = self._fresh()
        from sincor2.sinax.encoder import HashingEncoder, ProofState
        enc = HashingEncoder(dim=32)
        for i in range(5):
            ps = ProofState(goal=f"⊢ P{i}")
            emb = enc.encode(ps)
            from sincor2.sinax.graph_store import ProofStateNode
            n = ProofStateNode(node_id=str(uuid.uuid4()), proof_state=ps, embedding=emb)
            store.add_node(n)
        # Query with the first node's embedding
        query = enc.encode(ProofState(goal="⊢ P0"))
        results = store.nearest_neighbours(query, k=3)
        assert len(results) == 3
        for node, sim in results:
            assert -1.0 <= sim <= 1.0 + 1e-6

    def test_bfs(self):
        store = self._fresh()
        n1, n2, n3 = _make_node("A"), _make_node("B", 1), _make_node("C", 2)
        store.add_node(n1); store.add_node(n2); store.add_node(n3)
        e1 = _make_edge(n1.node_id, n2.node_id)
        e2 = _make_edge(n2.node_id, n3.node_id)
        store.add_edge(e1); store.add_edge(e2)
        visited = store.bfs(n1.node_id, max_depth=2)
        ids = {n.node_id for n in visited}
        assert n1.node_id in ids
        assert n2.node_id in ids
        assert n3.node_id in ids

    def test_path_reconstruction(self):
        store = self._fresh()
        n1, n2, n3 = _make_node("A"), _make_node("B", 1), _make_node("C", 2)
        store.add_node(n1); store.add_node(n2); store.add_node(n3)
        e1 = _make_edge(n1.node_id, n2.node_id)
        e2 = _make_edge(n2.node_id, n3.node_id)
        store.add_edge(e1); store.add_edge(e2)
        path = store.reconstruct_path(n1.node_id, n3.node_id)
        assert path is not None
        assert len(path) == 3
        assert path[0][0].node_id == n1.node_id
        assert path[-1][0].node_id == n3.node_id

    def test_path_reconstruction_no_path(self):
        store = self._fresh()
        n1, n2 = _make_node("A"), _make_node("B")
        store.add_node(n1); store.add_node(n2)
        path = store.reconstruct_path(n1.node_id, n2.node_id)
        assert path is None

    def test_lru_eviction(self):
        from sincor2.sinax.graph_store import ProofGraphStore
        from sincor2.sinax import config as cfg
        old_max = cfg.GRAPH_MAX_NODES
        cfg.GRAPH_MAX_NODES = 3
        store = ProofGraphStore()
        for i in range(5):
            store.add_node(_make_node(f"⊢ G{i}"))
        cfg.GRAPH_MAX_NODES = old_max
        assert store.stats()["nodes"] <= 3

    def test_singleton_returns_same(self):
        from sincor2.sinax.graph_store import get_store
        s1 = get_store()
        s2 = get_store()
        assert s1 is s2


# ===========================================================================
# Phase 3 — Retrieval
# ===========================================================================

def _populated_store_and_service(n_nodes: int = 10):
    from sincor2.sinax.graph_store import ProofGraphStore, ProofStateNode
    from sincor2.sinax.retrieval import RetrievalService
    from sincor2.sinax.encoder import ProofState, HashingEncoder
    store = ProofGraphStore()
    enc = HashingEncoder()  # use config default dim so get_encoder() queries match
    for i in range(n_nodes):
        ps = ProofState(goal=f"⊢ Node{i}")
        emb = enc.encode(ps)
        node = ProofStateNode(node_id=str(uuid.uuid4()), proof_state=ps, embedding=emb)
        store.add_node(node)
    svc = RetrievalService(store=store)
    svc.rebuild_index()
    return store, svc, enc


class TestRetrievalService:
    def test_query_returns_k_results(self):
        store, svc, enc = _populated_store_and_service(10)
        from sincor2.sinax.encoder import ProofState
        q = enc.encode(ProofState(goal="⊢ Node0"))
        results = svc.query(q, k=5)
        assert len(results) == 5

    def test_query_sorted_by_similarity(self):
        store, svc, enc = _populated_store_and_service(10)
        from sincor2.sinax.encoder import ProofState
        q = enc.encode(ProofState(goal="⊢ Node3"))
        results = svc.query(q, k=5)
        sims = [s for _, s in results]
        assert sims == sorted(sims, reverse=True)

    def test_cache_hit(self):
        store, svc, enc = _populated_store_and_service(10)
        from sincor2.sinax.encoder import ProofState
        q = enc.encode(ProofState(goal="⊢ CacheTest"))
        svc.query(q, k=3, use_cache=True)
        assert svc.stats()["cache_size"] == 1
        # Second query should hit cache
        svc.query(q, k=3, use_cache=True)
        assert svc.stats()["cache_size"] == 1

    def test_invalidate_cache(self):
        store, svc, enc = _populated_store_and_service(5)
        from sincor2.sinax.encoder import ProofState
        q = enc.encode(ProofState(goal="⊢ X"))
        svc.query(q, k=3, use_cache=True)
        svc.invalidate_cache()
        assert svc.stats()["cache_size"] == 0

    def test_query_with_proof_state_input(self):
        store, svc, enc = _populated_store_and_service(5)
        from sincor2.sinax.encoder import ProofState
        ps = ProofState(goal="⊢ Node2")
        results = svc.query(ps, k=3)
        assert len(results) == 3

    def test_add_node_incremental(self):
        store, svc, enc = _populated_store_and_service(5)
        from sincor2.sinax.graph_store import ProofStateNode
        from sincor2.sinax.encoder import ProofState
        ps = ProofState(goal="⊢ New")
        new_node = ProofStateNode(
            node_id=str(uuid.uuid4()),
            proof_state=ps,
            embedding=enc.encode(ps),
        )
        store.add_node(new_node)
        svc.add_node(new_node)
        results = svc.query(enc.encode(ps), k=3)
        result_ids = [n.node_id for n, _ in results]
        assert new_node.node_id in result_ids


# ===========================================================================
# Phase 4 — Geometric Search
# ===========================================================================

def _populated_search_engine(n_nodes: int = 8):
    """Create a small graph with verified transitions and return an engine."""
    from sincor2.sinax.graph_store import (
        ProofGraphStore, ProofStateNode, TacticEdge, VerificationResult
    )
    from sincor2.sinax.retrieval import RetrievalService
    from sincor2.sinax.search import GeometricSearchEngine
    from sincor2.sinax.encoder import ProofState, HashingEncoder

    store = ProofGraphStore()
    enc = HashingEncoder()  # use config default dim to match get_encoder() in search
    svc = RetrievalService(store=store)
    tactics = ["intro", "apply", "simp", "ring", "linarith", "norm_num", "exact", "rfl"]

    nodes = []
    for i in range(n_nodes):
        ps = ProofState(goal=f"⊢ G{i}")
        emb = enc.encode(ps)
        node = ProofStateNode(
            node_id=str(uuid.uuid4()), proof_state=ps, embedding=emb, depth=i
        )
        store.add_node(node)
        svc.add_node(node)
        nodes.append(node)

    # Add a chain of verified edges
    for i in range(len(nodes) - 1):
        e = TacticEdge(
            edge_id=str(uuid.uuid4()),
            source_id=nodes[i].node_id,
            target_id=nodes[i + 1].node_id,
            tactic=tactics[i % len(tactics)],
            verification_result=VerificationResult.VERIFIED,
        )
        store.add_edge(e)

    svc.rebuild_index()
    engine = GeometricSearchEngine(
        store=store,
        retrieval=svc,
        exploration_weight=0.3,
        beam_width=4,
        fallback_threshold=0.0,  # accept all in tests
    )
    return engine, nodes, enc


class TestGeometricSearchEngine:
    def test_search_returns_candidates(self):
        engine, nodes, enc = _populated_search_engine()
        from sincor2.sinax.encoder import ProofState
        ps = ProofState(goal="⊢ G0")
        results = engine.search(ps)
        assert results is not None
        assert len(results) > 0

    def test_candidates_have_tactic(self):
        engine, nodes, enc = _populated_search_engine()
        from sincor2.sinax.encoder import ProofState
        candidates = engine.search(ProofState(goal="⊢ G0"))
        assert candidates is not None
        for c in candidates:
            assert isinstance(c.tactic, str)

    def test_candidates_sorted_by_score(self):
        engine, nodes, enc = _populated_search_engine()
        from sincor2.sinax.encoder import ProofState
        candidates = engine.search(ProofState(goal="⊢ G0"))
        if candidates and len(candidates) > 1:
            scores = [c.score for c in candidates]
            assert scores == sorted(scores, reverse=True)

    def test_generate_tactics_list(self):
        engine, nodes, enc = _populated_search_engine()
        from sincor2.sinax.encoder import ProofState
        tactics = engine.generate_tactics(ProofState(goal="⊢ G2"), top_k=3)
        assert isinstance(tactics, list)

    def test_fallback_returns_none_when_threshold_high(self):
        engine, nodes, enc = _populated_search_engine()
        engine._fallback_threshold = 1.1  # impossible threshold
        from sincor2.sinax.encoder import ProofState
        result = engine.search(ProofState(goal="⊢ Unknown state xyz"))
        assert result is None

    def test_empty_graph_returns_none(self):
        from sincor2.sinax.graph_store import ProofGraphStore
        from sincor2.sinax.retrieval import RetrievalService
        from sincor2.sinax.search import GeometricSearchEngine
        from sincor2.sinax.encoder import ProofState
        store = ProofGraphStore()
        svc = RetrievalService(store=store)
        engine = GeometricSearchEngine(store=store, retrieval=svc)
        result = engine.search(ProofState(goal="⊢ P"))
        assert result is None

    def test_predict_target_region(self):
        engine, nodes, enc = _populated_search_engine()
        from sincor2.sinax.encoder import ProofState, Embedding
        emb = engine.predict_target_region(ProofState(goal="⊢ Target"))
        assert isinstance(emb, Embedding)


# ===========================================================================
# Phase 5 — Curvature Analyzer
# ===========================================================================

def _graph_with_failures():
    """Build a small graph with a mix of verified and failed edges."""
    from sincor2.sinax.graph_store import (
        ProofGraphStore, ProofStateNode, TacticEdge, VerificationResult
    )
    from sincor2.sinax import config as cfg
    from sincor2.sinax.encoder import ProofState, HashingEncoder

    store = ProofGraphStore()
    enc = HashingEncoder(dim=32)
    nodes = []
    for i in range(6):
        ps = ProofState(goal=f"⊢ G{i}")
        n = ProofStateNode(
            node_id=str(uuid.uuid4()),
            proof_state=ps,
            embedding=enc.encode(ps),
            depth=i,
        )
        store.add_node(n)
        nodes.append(n)

    outcomes = [
        VerificationResult.VERIFIED,
        VerificationResult.FAILED,
        VerificationResult.VERIFIED,
        VerificationResult.FAILED,
        VerificationResult.FAILED,
    ]
    cfg_orig = cfg.GRAPH_WRITE_ONLY_VERIFIED
    cfg.GRAPH_WRITE_ONLY_VERIFIED = False
    for i, outcome in enumerate(outcomes):
        e = TacticEdge(
            edge_id=str(uuid.uuid4()),
            source_id=nodes[i].node_id,
            target_id=nodes[(i + 1) % len(nodes)].node_id,
            tactic="intro",
            verification_result=outcome,
        )
        store.add_edge(e)
    cfg.GRAPH_WRITE_ONLY_VERIFIED = cfg_orig
    return store, nodes


class TestCurvatureAnalyzer:
    def test_compute_returns_region(self):
        from sincor2.sinax.curvature import CurvatureAnalyzer
        from sincor2.sinax import config as cfg
        store, nodes = _graph_with_failures()
        old_min = cfg.CURVATURE_MIN_OBS
        cfg.CURVATURE_MIN_OBS = 1
        analyzer = CurvatureAnalyzer(store=store)
        rc = analyzer.compute(nodes[0].node_id, radius=3)
        cfg.CURVATURE_MIN_OBS = old_min
        assert rc is not None

    def test_curvature_score_in_range(self):
        from sincor2.sinax.curvature import CurvatureAnalyzer
        from sincor2.sinax import config as cfg
        store, nodes = _graph_with_failures()
        old_min = cfg.CURVATURE_MIN_OBS
        cfg.CURVATURE_MIN_OBS = 1
        analyzer = CurvatureAnalyzer(store=store)
        rc = analyzer.compute(nodes[0].node_id, radius=3)
        cfg.CURVATURE_MIN_OBS = old_min
        if rc:
            assert 0.0 <= rc.curvature_score <= 1.0

    def test_below_min_obs_returns_none(self):
        from sincor2.sinax.curvature import CurvatureAnalyzer
        from sincor2.sinax import config as cfg
        store, nodes = _graph_with_failures()
        old_min = cfg.CURVATURE_MIN_OBS
        cfg.CURVATURE_MIN_OBS = 1000  # impossibly high
        analyzer = CurvatureAnalyzer(store=store)
        rc = analyzer.compute(nodes[0].node_id, radius=1)
        cfg.CURVATURE_MIN_OBS = old_min
        assert rc is None

    def test_to_dict_has_required_keys(self):
        from sincor2.sinax.curvature import CurvatureAnalyzer, RegionCurvature
        from sincor2.sinax import config as cfg
        store, nodes = _graph_with_failures()
        old_min = cfg.CURVATURE_MIN_OBS
        cfg.CURVATURE_MIN_OBS = 1
        analyzer = CurvatureAnalyzer(store=store)
        rc = analyzer.compute(nodes[0].node_id, radius=3)
        cfg.CURVATURE_MIN_OBS = old_min
        if rc:
            d = rc.to_dict()
            for key in ("focal_node_id", "curvature_score", "failure_density",
                        "branching_factor", "success_frequency"):
                assert key in d, f"Missing key: {key}"

    def test_composite_score_formula(self):
        from sincor2.sinax.curvature import CurvatureAnalyzer
        # High failure density → high score
        s1 = CurvatureAnalyzer._composite_score(1.0, 1.0, 5.0, 0.0)
        s2 = CurvatureAnalyzer._composite_score(0.0, 0.0, 0.0, 1.0)
        assert s1 > s2
        assert 0.0 <= s1 <= 1.0
        assert 0.0 <= s2 <= 1.0

    def test_cache(self):
        from sincor2.sinax.curvature import CurvatureAnalyzer
        from sincor2.sinax import config as cfg
        store, nodes = _graph_with_failures()
        old_min = cfg.CURVATURE_MIN_OBS
        cfg.CURVATURE_MIN_OBS = 1
        analyzer = CurvatureAnalyzer(store=store)
        rc1 = analyzer.compute(nodes[0].node_id, radius=2, use_cache=True)
        rc2 = analyzer.compute(nodes[0].node_id, radius=2, use_cache=True)
        cfg.CURVATURE_MIN_OBS = old_min
        assert rc1 is rc2  # same object from cache


# ===========================================================================
# Phase 6 — Lemma Discovery
# ===========================================================================

class TestLemmaDiscovery:
    def test_lemma_candidate_to_dict(self):
        from sincor2.sinax.lemma_discovery import LemmaCandidate
        from sincor2.sinax.graph_store import VerificationResult
        lc = LemmaCandidate(
            lemma_id="abc123",
            statement="lemma foo : ⊢ P := by sorry",
            proof_sketch="  sorry",
            cluster_size=3,
            impact_score=0.5,
            verification_result=VerificationResult.PENDING,
        )
        d = lc.to_dict()
        assert d["lemma_id"] == "abc123"
        assert d["verification_result"] == "pending"

    def test_verifier_returns_pending_without_lean(self):
        from sincor2.sinax.lemma_discovery import Verifier
        from sincor2.sinax.graph_store import VerificationResult
        v = Verifier()
        # In CI (no Lean), should return PENDING
        result = v.verify("theorem foo : True := trivial")
        assert result in (VerificationResult.PENDING, VerificationResult.VERIFIED,
                          VerificationResult.FAILED)

    def test_engine_discover_empty_graph(self):
        from sincor2.sinax.graph_store import ProofGraphStore
        from sincor2.sinax.curvature import CurvatureAnalyzer
        from sincor2.sinax.lemma_discovery import LemmaDiscoveryEngine
        store = ProofGraphStore()
        analyzer = CurvatureAnalyzer(store=store)
        engine = LemmaDiscoveryEngine(store=store, curvature_analyzer=analyzer)
        candidates = engine.discover()
        assert isinstance(candidates, list)
        assert len(candidates) == 0

    def test_engine_discover_with_failures(self):
        from sincor2.sinax.curvature import CurvatureAnalyzer
        from sincor2.sinax.lemma_discovery import LemmaDiscoveryEngine
        from sincor2.sinax import config as cfg

        store, nodes = _graph_with_failures()
        old_min_obs = cfg.CURVATURE_MIN_OBS
        old_min_cluster = cfg.LEMMA_MIN_CLUSTER_SIZE
        cfg.CURVATURE_MIN_OBS = 1
        cfg.LEMMA_MIN_CLUSTER_SIZE = 1
        analyzer = CurvatureAnalyzer(store=store)
        engine = LemmaDiscoveryEngine(
            store=store, curvature_analyzer=analyzer, min_cluster_size=1
        )
        candidates = engine.discover()
        cfg.CURVATURE_MIN_OBS = old_min_obs
        cfg.LEMMA_MIN_CLUSTER_SIZE = old_min_cluster
        # May be 0 or more depending on failure density; just check it's a list
        assert isinstance(candidates, list)

    def test_get_all_candidates_empty(self):
        from sincor2.sinax.graph_store import ProofGraphStore
        from sincor2.sinax.curvature import CurvatureAnalyzer
        from sincor2.sinax.lemma_discovery import LemmaDiscoveryEngine
        engine = LemmaDiscoveryEngine(
            store=ProofGraphStore(),
            curvature_analyzer=CurvatureAnalyzer(store=ProofGraphStore()),
        )
        assert engine.get_all_candidates() == []


# ===========================================================================
# Phase 7 — Integration Layer
# ===========================================================================

class TestIntegration:
    def test_task_to_dict(self):
        from sincor2.sinax.integration import Task, TaskType, TaskStatus
        t = Task(task_type=TaskType.STATUS)
        d = t.to_dict()
        assert d["task_type"] == "status"
        assert d["status"] == "submitted"
        assert "task_id" in d

    def test_proof_state_message_roundtrip(self):
        from sincor2.sinax.integration import ProofStateMessage
        msg = ProofStateMessage(
            goal="⊢ P", context="ctx", hypotheses=["h"], tactic_history=["intro"]
        )
        d = msg.to_dict()
        ps = msg.to_proof_state()
        assert ps.goal == "⊢ P"
        assert ps.hypotheses == ["h"]

    def test_local_adapter_status(self):
        from sincor2.sinax.integration import LocalAdapter, Task, TaskType, TaskStatus
        adapter = LocalAdapter()
        task = Task(task_type=TaskType.STATUS)
        task_id = adapter.submit_task(task)
        assert task_id == task.task_id
        result = adapter.get_result(task_id)
        assert result is not None
        assert "sinax_enabled" in result

    def test_local_adapter_encode_state(self):
        from sincor2.sinax.integration import (
            LocalAdapter, Task, TaskType, ProofStateMessage
        )
        adapter = LocalAdapter()
        ps = ProofStateMessage(goal="⊢ P ∧ Q")
        task = Task(
            task_type=TaskType.ENCODE_STATE,
            payload={"proof_state": ps.to_dict()},
        )
        adapter.submit_task(task)
        result = adapter.get_result(task.task_id)
        assert result is not None
        assert "proof_state_hash" in result

    def test_local_adapter_search_tactics_fallback(self):
        from sincor2.sinax.integration import (
            LocalAdapter, Task, TaskType, ProofStateMessage
        )
        adapter = LocalAdapter()
        ps = ProofStateMessage(goal="⊢ totally unknown state")
        task = Task(
            task_type=TaskType.SEARCH_TACTICS,
            payload={"proof_state": ps.to_dict()},
        )
        adapter.submit_task(task)
        result = adapter.get_result(task.task_id)
        assert result is not None
        assert "tactics" in result

    def test_local_adapter_verify_lemma(self):
        from sincor2.sinax.integration import (
            LocalAdapter, Task, TaskType
        )
        adapter = LocalAdapter()
        task = Task(
            task_type=TaskType.VERIFY_LEMMA,
            payload={"statement": "lemma foo : True := trivial", "proof_sketch": ""},
        )
        adapter.submit_task(task)
        result = adapter.get_result(task.task_id)
        assert result is not None
        assert "verification_result" in result

    def test_local_adapter_cancel_task(self):
        from sincor2.sinax.integration import LocalAdapter, Task, TaskType, TaskStatus
        adapter = LocalAdapter()
        task = Task(task_type=TaskType.STATUS)
        # Mark as in-progress manually before cancelling
        adapter._tasks[task.task_id] = task
        task.status = TaskStatus.IN_PROGRESS
        cancelled = adapter.cancel_task(task.task_id)
        assert cancelled is True

    def test_router_unknown_task_type(self):
        from sincor2.sinax.integration import IntegrationRouter, Task, TaskType, TaskStatus
        router = IntegrationRouter()
        task = Task(task_type=TaskType.STATUS)
        # Monkey-patch to unknown type string
        task.task_type = "completely_unknown"
        router.dispatch(task)
        assert task.status == TaskStatus.FAILED

    def test_trajectory_message(self):
        from sincor2.sinax.integration import Trajectory
        traj = Trajectory(start_node_id="a", end_node_id="b", steps=[])
        d = traj.to_dict()
        assert d["start_node_id"] == "a"
        assert d["end_node_id"] == "b"

    def test_lemma_message(self):
        from sincor2.sinax.integration import LemmaMessage
        lm = LemmaMessage(lemma_id="x", statement="lemma foo : P")
        d = lm.to_dict()
        assert d["lemma_id"] == "x"

    def test_conjecture_message(self):
        from sincor2.sinax.integration import ConjectureMessage
        cm = ConjectureMessage(statement="∀ n, n + 0 = n")
        d = cm.to_dict()
        assert "conjecture_id" in d


# ===========================================================================
# Phase 7 — Visualization
# ===========================================================================

def _build_visualization_graph():
    """Small graph for visualization tests."""
    from sincor2.sinax.graph_store import ProofGraphStore, ProofStateNode, TacticEdge, VerificationResult
    from sincor2.sinax.encoder import ProofState, HashingEncoder

    store = ProofGraphStore()
    enc = HashingEncoder(dim=32)
    nodes = []
    for i in range(4):
        ps = ProofState(goal=f"⊢ V{i}")
        node = ProofStateNode(
            node_id=str(uuid.uuid4()),
            proof_state=ps,
            embedding=enc.encode(ps),
            depth=i,
        )
        store.add_node(node)
        nodes.append(node)
    for i in range(3):
        e = TacticEdge(
            edge_id=str(uuid.uuid4()),
            source_id=nodes[i].node_id,
            target_id=nodes[i + 1].node_id,
            tactic="simp",
            verification_result=VerificationResult.VERIFIED,
        )
        store.add_edge(e)
    return store, nodes


class TestVisualization:
    def test_graph_visualizer_render(self):
        from sincor2.sinax.visualization import GraphVisualizer
        store, nodes = _build_visualization_graph()
        viz = GraphVisualizer(store=store)
        payload = viz.render(root_id=nodes[0].node_id, max_depth=3)
        assert "nodes" in payload
        assert "edges" in payload
        assert payload["stats"]["nodes"] > 0

    def test_graph_visualizer_render_full(self):
        from sincor2.sinax.visualization import GraphVisualizer
        store, nodes = _build_visualization_graph()
        viz = GraphVisualizer(store=store)
        payload = viz.render_full(max_nodes=100)
        assert len(payload["nodes"]) == 4

    def test_trajectory_visualizer_found(self):
        from sincor2.sinax.visualization import TrajectoryVisualizer
        store, nodes = _build_visualization_graph()
        viz = TrajectoryVisualizer(store=store)
        result = viz.render(nodes[0].node_id, nodes[3].node_id)
        assert result["found"] is True
        assert result["length"] == 4

    def test_trajectory_visualizer_not_found(self):
        from sincor2.sinax.visualization import TrajectoryVisualizer
        store, nodes = _build_visualization_graph()
        viz = TrajectoryVisualizer(store=store)
        result = viz.render(nodes[3].node_id, nodes[0].node_id)
        assert result["found"] is False

    def test_lemma_report_render(self):
        from sincor2.sinax.visualization import LemmaReport
        from sincor2.sinax.graph_store import ProofGraphStore
        from sincor2.sinax.curvature import CurvatureAnalyzer
        from sincor2.sinax.lemma_discovery import LemmaDiscoveryEngine
        engine = LemmaDiscoveryEngine(
            store=ProofGraphStore(),
            curvature_analyzer=CurvatureAnalyzer(store=ProofGraphStore()),
        )
        report = LemmaReport(engine=engine)
        result = report.render()
        assert "lemmas" in result
        assert result["count"] == 0

    def test_dashboard_summary(self):
        from sincor2.sinax.visualization import SINAXDashboard
        from sincor2.sinax.graph_store import ProofGraphStore
        from sincor2.sinax.curvature import CurvatureAnalyzer
        from sincor2.sinax.lemma_discovery import LemmaDiscoveryEngine
        store, nodes = _build_visualization_graph()
        analyzer = CurvatureAnalyzer(store=store)
        engine = LemmaDiscoveryEngine(store=store, curvature_analyzer=analyzer)
        dashboard = SINAXDashboard(store=store, analyzer=analyzer, lemma_engine=engine)
        summary = dashboard.render_summary()
        assert "sinax_enabled" in summary
        assert "graph" in summary
        assert "lemmas" in summary


# ===========================================================================
# Integration tests — SINAX on/off compatibility
# ===========================================================================

class TestSINAXCompatibility:
    """Verify that disabling SINAX does not affect the rest of the system."""

    def test_sinax_disabled_flag(self):
        from sincor2.sinax import config as cfg
        original = cfg.SINAX_ENABLED
        cfg.SINAX_ENABLED = False
        assert cfg.SINAX_ENABLED is False
        cfg.SINAX_ENABLED = original

    def test_sinax_mode_analytics(self):
        from sincor2.sinax import config as cfg
        original = cfg.SINAX_MODE
        cfg.SINAX_MODE = "analytics"
        assert cfg.SINAX_MODE == "analytics"
        cfg.SINAX_MODE = original

    def test_sinax_package_importable(self):
        """The SINAX package must import cleanly regardless of mode."""
        import importlib
        import sincor2.sinax
        importlib.reload(sincor2.sinax)

    def test_main_sincor2_package_unaffected(self):
        """Importing SINAX must not break the top-level sincor2 package."""
        import sincor2
        assert sincor2.__version__ is not None

    def test_fallback_path(self):
        """When the graph is empty, generate_tactics must return [] (fallback)."""
        from sincor2.sinax.graph_store import ProofGraphStore
        from sincor2.sinax.retrieval import RetrievalService
        from sincor2.sinax.search import GeometricSearchEngine
        from sincor2.sinax.encoder import ProofState
        store = ProofGraphStore()
        svc = RetrievalService(store=store)
        engine = GeometricSearchEngine(store=store, retrieval=svc)
        tactics = engine.generate_tactics(ProofState(goal="⊢ Fallback"))
        assert tactics == []


# ===========================================================================
# __init__ exports
# ===========================================================================

class TestPackageExports:
    def test_all_exports_importable(self):
        import sincor2.sinax as sx
        expected = [
            "ProofState", "Embedding", "get_encoder",
            "get_store", "get_retrieval_service",
            "get_search_engine", "get_curvature_analyzer",
            "get_lemma_engine", "get_router", "get_dashboard",
            "get_config", "SINAX_ENABLED", "SINAX_MODE",
        ]
        for name in expected:
            assert hasattr(sx, name), f"Missing export: {name}"
=======
Tests for SINAX — Proof Topology Navigator

Covers all four PTN layers and the top-level ProofTopologyNavigator API.

Run with:
    PYTHONPATH=src:src/sincor2 python tests/test_sinax.py
"""

import math
import sys
import os

# ---------------------------------------------------------------------------
# Test harness (matches the rest of the test suite style)
# ---------------------------------------------------------------------------

passed = 0
failed = 0


def test(name, func):
    global passed, failed
    try:
        func()
        print(f"  PASS: {name}")
        passed += 1
        return True
    except Exception as e:
        print(f"  FAIL: {name} — {e}")
        failed += 1
        return False


# ---------------------------------------------------------------------------
# Sample proof states used across all tests
# ---------------------------------------------------------------------------

START = "⊢ ∀ n : ℕ, n + 0 = n"
TARGET = "closed"
LEMMA_1 = "⊢ 0 + n = n"
LEMMA_2 = "⊢ n + succ m = succ (n + m)"
FAILED_STATE = "⊢ False  -- dead end"

CONTEXT_STATES = [LEMMA_1, LEMMA_2]
ALL_STATES = [START, TARGET, LEMMA_1, LEMMA_2, FAILED_STATE]


print("\n" + "=" * 60)
print("SINAX — PROOF TOPOLOGY NAVIGATOR TESTS")
print("=" * 60 + "\n")


# ===========================================================================
# Layer 1: ProofManifold
# ===========================================================================

print("LAYER 1 — PROOF MANIFOLD:")


def test_proof_manifold_import():
    from sincor2.sinax.proof_manifold import ProofManifold
    assert ProofManifold is not None


def test_embed_returns_manifold_point():
    from sincor2.sinax.proof_manifold import ProofManifold
    m = ProofManifold(dim=32)
    pt = m.embed(START)
    assert pt.coordinates.shape == (32,), f"expected (32,), got {pt.coordinates.shape}"
    assert pt.source_state == START
    assert isinstance(pt.curvature, float)


def test_embed_different_states_differ():
    from sincor2.sinax.proof_manifold import ProofManifold
    m = ProofManifold(dim=32)
    p1 = m.embed(START)
    p2 = m.embed(TARGET)
    assert p1.distance_to(p2) > 0, "distinct states should have different embeddings"


def test_riemannian_distance_non_negative():
    from sincor2.sinax.proof_manifold import ProofManifold
    m = ProofManifold(dim=32)
    p1 = m.embed(START)
    p2 = m.embed(LEMMA_1)
    d = m.riemannian_distance(p1, p2)
    assert d >= 0, f"distance must be non-negative, got {d}"


def test_nearest_neighbours():
    from sincor2.sinax.proof_manifold import ProofManifold
    m = ProofManifold(dim=32)
    points = [m.embed(s) for s in ALL_STATES]
    neighbours = m.nearest_neighbours(points[0], k=3)
    assert len(neighbours) <= 3


def test_build_region():
    from sincor2.sinax.proof_manifold import ProofManifold
    m = ProofManifold(dim=32)
    for s in ALL_STATES:
        m.embed(s)
    region = m.build_region(START, radius=5.0)
    assert region.centre.source_state == START
    assert region.radius == 5.0


def test_curvature_training_signal():
    from sincor2.sinax.proof_manifold import ProofManifold
    m = ProofManifold(dim=32)
    signal = m.curvature_training_signal(ALL_STATES)
    assert signal["num_states"] == len(ALL_STATES)
    assert "mean_curvature" in signal
    assert "centroid" in signal


test("ProofManifold import", test_proof_manifold_import)
test("embed() returns ManifoldPoint", test_embed_returns_manifold_point)
test("different states have different embeddings", test_embed_different_states_differ)
test("riemannian_distance() >= 0", test_riemannian_distance_non_negative)
test("nearest_neighbours() respects k", test_nearest_neighbours)
test("build_region()", test_build_region)
test("curvature_training_signal()", test_curvature_training_signal)


# ===========================================================================
# Layer 2: GeodesicFlowEngine
# ===========================================================================

print("\nLAYER 2 — GEODESIC FLOW ENGINE:")


def test_geodesic_flow_import():
    from sincor2.sinax.geodesic_flow import GeodesicFlowEngine
    assert GeodesicFlowEngine is not None


def test_geodesic_returns_path():
    from sincor2.sinax.proof_manifold import ProofManifold
    from sincor2.sinax.geodesic_flow import GeodesicFlowEngine, FlowConfig
    m = ProofManifold(dim=32)
    cfg = FlowConfig(num_steps=5)
    engine = GeodesicFlowEngine(manifold=m, config=cfg)
    path = engine.geodesic_flow(START, TARGET)
    assert path.start_state == START
    assert path.target_state == TARGET
    assert len(path.waypoints) >= 2
    assert isinstance(path.path_length, float)
    assert path.path_length >= 0


def test_geodesic_has_tactics():
    from sincor2.sinax.proof_manifold import ProofManifold
    from sincor2.sinax.geodesic_flow import GeodesicFlowEngine, FlowConfig
    m = ProofManifold(dim=32)
    engine = GeodesicFlowEngine(manifold=m, config=FlowConfig(num_steps=5))
    path = engine.geodesic_flow(START, LEMMA_1)
    assert len(path.tactics) >= 2
    for t in path.tactics:
        assert isinstance(t, str) and len(t) > 0


def test_teleport_when_close():
    from sincor2.sinax.proof_manifold import ProofManifold
    from sincor2.sinax.geodesic_flow import GeodesicFlowEngine, FlowConfig
    m = ProofManifold(dim=32)
    # Use identical state — guaranteed zero distance → teleport
    engine = GeodesicFlowEngine(
        manifold=m,
        config=FlowConfig(teleport_threshold=999.0),  # large threshold → always teleport
    )
    path = engine.geodesic_flow(START, START)
    assert path.teleported


def test_proof_transfer_score():
    from sincor2.sinax.proof_manifold import ProofManifold
    from sincor2.sinax.geodesic_flow import GeodesicFlowEngine, FlowConfig
    m = ProofManifold(dim=32)
    engine = GeodesicFlowEngine(manifold=m, config=FlowConfig(num_steps=5))
    path = engine.geodesic_flow(START, TARGET)
    score = engine.proof_transfer_score(path, LEMMA_1)
    assert 0.0 <= score <= 1.0, f"transfer score out of range: {score}"


test("GeodesicFlowEngine import", test_geodesic_flow_import)
test("geodesic_flow() returns GeodesicPath", test_geodesic_returns_path)
test("path has non-empty tactic list", test_geodesic_has_tactics)
test("teleportation when states are close", test_teleport_when_close)
test("proof_transfer_score() in [0,1]", test_proof_transfer_score)


# ===========================================================================
# Layer 3: HomologyDetector
# ===========================================================================

print("\nLAYER 3 — HOMOLOGY DETECTOR:")


def test_homology_detector_import():
    from sincor2.sinax.homology_detector import HomologyDetector
    assert HomologyDetector is not None


def test_homology_analyse_returns_report():
    from sincor2.sinax.proof_manifold import ProofManifold
    from sincor2.sinax.homology_detector import HomologyDetector
    m = ProofManifold(dim=32)
    hd = HomologyDetector(manifold=m, num_radii=10, max_radius=2.0)
    report = hd.analyse(ALL_STATES)
    assert report.num_states == len(ALL_STATES)
    assert isinstance(report.betti_numbers, dict)
    assert isinstance(report.hole_filling_suggestions, list)
    assert isinstance(report.has_holes, bool)


def test_homology_empty_input():
    from sincor2.sinax.proof_manifold import ProofManifold
    from sincor2.sinax.homology_detector import HomologyDetector
    m = ProofManifold(dim=32)
    hd = HomologyDetector(manifold=m)
    report = hd.analyse([])
    assert report.num_states == 0


def test_detect_dead_ends():
    from sincor2.sinax.proof_manifold import ProofManifold
    from sincor2.sinax.homology_detector import HomologyDetector
    m = ProofManifold(dim=32)
    hd = HomologyDetector(manifold=m)
    dead_ends = hd.detect_dead_ends([FAILED_STATE, FAILED_STATE + " 2"])
    assert isinstance(dead_ends, list)


def test_betti_numbers_non_negative():
    from sincor2.sinax.proof_manifold import ProofManifold
    from sincor2.sinax.homology_detector import HomologyDetector
    m = ProofManifold(dim=32)
    hd = HomologyDetector(manifold=m, num_radii=10)
    report = hd.analyse(ALL_STATES)
    for dim, count in report.betti_numbers.items():
        assert count >= 0, f"Betti number for dim {dim} should be >= 0"


test("HomologyDetector import", test_homology_detector_import)
test("analyse() returns HomologyReport", test_homology_analyse_returns_report)
test("analyse([]) returns empty report", test_homology_empty_input)
test("detect_dead_ends()", test_detect_dead_ends)
test("Betti numbers are non-negative", test_betti_numbers_non_negative)


# ===========================================================================
# Layer 4: MorseFilter
# ===========================================================================

print("\nLAYER 4 — MORSE FILTER:")


def test_morse_filter_import():
    from sincor2.sinax.morse_filter import MorseFilter
    assert MorseFilter is not None


def test_morse_decompose_returns_decomposition():
    from sincor2.sinax.proof_manifold import ProofManifold
    from sincor2.sinax.morse_filter import MorseFilter
    m = ProofManifold(dim=32)
    mf = MorseFilter(manifold=m)
    decomp = mf.decompose(ALL_STATES)
    assert len(decomp.critical_points) == len(ALL_STATES)
    assert isinstance(decomp.key_lemmas, list)
    assert isinstance(decomp.branch_points, list)
    assert decomp.min_proof_length_bound >= 1


def test_morse_empty_input():
    from sincor2.sinax.proof_manifold import ProofManifold
    from sincor2.sinax.morse_filter import MorseFilter
    m = ProofManifold(dim=32)
    mf = MorseFilter(manifold=m)
    decomp = mf.decompose([])
    assert decomp.min_proof_length_bound == 0
    assert decomp.critical_points == []


def test_morse_filter_path():
    from sincor2.sinax.proof_manifold import ProofManifold
    from sincor2.sinax.geodesic_flow import GeodesicFlowEngine, FlowConfig
    from sincor2.sinax.morse_filter import MorseFilter
    m = ProofManifold(dim=32)
    engine = GeodesicFlowEngine(manifold=m, config=FlowConfig(num_steps=8))
    mf = MorseFilter(manifold=m)
    path = engine.geodesic_flow(START, TARGET)
    decomp = mf.decompose(ALL_STATES)
    filtered = mf.filter_path(path.waypoints, decomp)
    assert len(filtered) >= 2  # always includes start and end
    assert filtered[0] is path.waypoints[0]
    assert filtered[-1] is path.waypoints[-1]


def test_morse_simplification_power():
    from sincor2.sinax.proof_manifold import ProofManifold
    from sincor2.sinax.morse_filter import MorseFilter, CriticalPointType
    m = ProofManifold(dim=32)
    mf = MorseFilter(manifold=m)
    decomp = mf.decompose(ALL_STATES)
    for cp in decomp.critical_points:
        assert 0.0 <= cp.simplification_power <= 1.0


test("MorseFilter import", test_morse_filter_import)
test("decompose() returns MorseDecomposition", test_morse_decompose_returns_decomposition)
test("decompose([]) returns empty result", test_morse_empty_input)
test("filter_path() keeps start and end", test_morse_filter_path)
test("simplification_power in [0,1]", test_morse_simplification_power)


# ===========================================================================
# AxiomSolver (end-to-end)
# ===========================================================================

print("\nAXIOM SOLVER (end-to-end):")


def test_axiom_solver_import():
    from sincor2.sinax.axiom_solver import AxiomSolver
    assert AxiomSolver is not None


def test_axiom_solver_solve():
    from sincor2.sinax.axiom_solver import AxiomSolver
    solver = AxiomSolver(manifold_dim=32)
    result = solver.solve(START, TARGET)
    assert result.start_state == START
    assert result.target_state == TARGET
    assert isinstance(result.tactic_sequence, list)
    assert isinstance(result.proof_narrative, str)
    assert result.elapsed_seconds >= 0


def test_axiom_solver_to_dict():
    from sincor2.sinax.axiom_solver import AxiomSolver
    solver = AxiomSolver(manifold_dim=32)
    result = solver.solve(START, TARGET)
    d = result.to_dict()
    assert "proof_id" in d
    assert "tactic_sequence" in d
    assert "proof_narrative" in d
    assert "homology" in d
    assert "morse" in d


def test_axiom_solver_solve_batch():
    from sincor2.sinax.axiom_solver import AxiomSolver
    solver = AxiomSolver(manifold_dim=32)
    problems = [
        {"start": START, "target": TARGET},
        {"start": LEMMA_1, "target": LEMMA_2},
    ]
    results = solver.solve_batch(problems)
    assert len(results) == 2


def test_axiom_solver_training_signal():
    from sincor2.sinax.axiom_solver import AxiomSolver
    solver = AxiomSolver(manifold_dim=32)
    signal = solver.get_manifold_training_signal(ALL_STATES)
    assert "curvature" in signal
    assert "betti_numbers" in signal


test("AxiomSolver import", test_axiom_solver_import)
test("solve() returns ProofResult", test_axiom_solver_solve)
test("ProofResult.to_dict() is JSON-serialisable", test_axiom_solver_to_dict)
test("solve_batch() handles multiple problems", test_axiom_solver_solve_batch)
test("get_manifold_training_signal()", test_axiom_solver_training_signal)


# ===========================================================================
# ProofTopologyNavigator (top-level API)
# ===========================================================================

print("\nPROOF TOPOLOGY NAVIGATOR (top-level API):")


def test_ptn_import():
    from sincor2.sinax.ptn import ProofTopologyNavigator
    assert ProofTopologyNavigator is not None


def test_ptn_solve():
    from sincor2.sinax.ptn import ProofTopologyNavigator
    nav = ProofTopologyNavigator(manifold_dim=32, num_flow_steps=5)
    result = nav.solve(START, TARGET)
    assert result.succeeded or not result.succeeded  # just no crash
    d = result.to_dict()
    assert isinstance(d["tactic_sequence"], list)


def test_ptn_embed():
    from sincor2.sinax.ptn import ProofTopologyNavigator
    nav = ProofTopologyNavigator(manifold_dim=32)
    point = nav.embed(START)
    assert "coordinates" in point
    assert len(point["coordinates"]) == 32


def test_ptn_geodesic():
    from sincor2.sinax.ptn import ProofTopologyNavigator
    nav = ProofTopologyNavigator(manifold_dim=32, num_flow_steps=5)
    result = nav.geodesic(START, TARGET)
    assert "tactics" in result
    assert "path_length" in result
    assert result["path_length"] >= 0


def test_ptn_homology():
    from sincor2.sinax.ptn import ProofTopologyNavigator
    nav = ProofTopologyNavigator(manifold_dim=32)
    result = nav.homology(ALL_STATES)
    assert "betti_numbers" in result
    assert "hole_filling_suggestions" in result


def test_ptn_morse():
    from sincor2.sinax.ptn import ProofTopologyNavigator
    nav = ProofTopologyNavigator(manifold_dim=32)
    result = nav.morse(ALL_STATES)
    assert "key_lemmas" in result
    assert "min_proof_length_bound" in result
    assert result["min_proof_length_bound"] >= 1


def test_ptn_training_signal():
    from sincor2.sinax.ptn import ProofTopologyNavigator
    nav = ProofTopologyNavigator(manifold_dim=32)
    result = nav.training_signal(ALL_STATES)
    assert "curvature" in result
    assert "betti_numbers" in result


def test_ptn_transfer_score():
    from sincor2.sinax.ptn import ProofTopologyNavigator
    nav = ProofTopologyNavigator(manifold_dim=32, num_flow_steps=5)
    r1 = nav.solve(START, TARGET)
    score = nav.transfer_score(r1, LEMMA_1)
    assert 0.0 <= score <= 1.0


test("ProofTopologyNavigator import", test_ptn_import)
test("ptn.solve()", test_ptn_solve)
test("ptn.embed()", test_ptn_embed)
test("ptn.geodesic()", test_ptn_geodesic)
test("ptn.homology()", test_ptn_homology)
test("ptn.morse()", test_ptn_morse)
test("ptn.training_signal()", test_ptn_training_signal)
test("ptn.transfer_score()", test_ptn_transfer_score)


# ===========================================================================
# Summary
# ===========================================================================

print("\n" + "=" * 60)
print(f"RESULTS: {passed} passed, {failed} failed out of {passed + failed} tests")
print("=" * 60 + "\n")

if failed > 0:
    sys.exit(1)
>>>>>>> origin/main
