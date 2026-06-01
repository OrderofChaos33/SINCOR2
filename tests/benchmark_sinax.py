#!/usr/bin/env python3
"""
SINAX Benchmark Suite
======================
Measures SINAX performance against baseline (AxiomSolver with no SINAX) on:
  1. Proof state encoding throughput
  2. K-NN retrieval latency
  3. Geometric search step time
  4. Curvature computation time
  5. Lemma discovery cycle time

Run with:
    PYTHONPATH=src:src/sincor2 python tests/benchmark_sinax.py

Outputs a table comparing baseline vs. SINAX-augmented metrics and a
summary JSON suitable for CI performance tracking.
"""

from __future__ import annotations

import json
import sys
import time
import uuid

import numpy as np

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _timer(fn, *args, **kwargs):
    """Return (result, elapsed_seconds)."""
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, time.perf_counter() - t0


def _fmt_ms(seconds: float) -> str:
    return f"{seconds * 1000:.2f} ms"


# ---------------------------------------------------------------------------
# Fixtures: build a proof graph with N nodes and verified/failed edges
# ---------------------------------------------------------------------------

def _build_benchmark_graph(n_nodes: int = 200, n_failures: int = 40):
    from sincor2.sinax.graph_store import (
        ProofGraphStore, ProofStateNode, TacticEdge, VerificationResult
    )
    from sincor2.sinax import config as cfg
    from sincor2.sinax.encoder import ProofState, HashingEncoder

    cfg.GRAPH_WRITE_ONLY_VERIFIED = False  # allow failure edges for benchmark

    store = ProofGraphStore()
    enc = HashingEncoder(dim=256)
    tactics = ["intro", "apply", "simp", "ring", "linarith", "exact", "rfl", "norm_num"]
    nodes = []
    for i in range(n_nodes):
        ps = ProofState(
            goal=f"⊢ Bench{i}",
            hypotheses=[f"h{j} : P{j}" for j in range(3)],
            tactic_history=tactics[:i % len(tactics)],
        )
        emb = enc.encode(ps)
        node = ProofStateNode(
            node_id=str(uuid.uuid4()), proof_state=ps, embedding=emb, depth=i % 10
        )
        store.add_node(node)
        nodes.append(node)

    # Add a forward chain + random failure edges
    rng = np.random.default_rng(42)
    for i in range(n_nodes - 1):
        verified = rng.random() > (n_failures / n_nodes)
        vr = VerificationResult.VERIFIED if verified else VerificationResult.FAILED
        e = TacticEdge(
            edge_id=str(uuid.uuid4()),
            source_id=nodes[i].node_id,
            target_id=nodes[(i + 1) % n_nodes].node_id,
            tactic=tactics[i % len(tactics)],
            verification_result=vr,
        )
        store.add_edge(e)

    cfg.GRAPH_WRITE_ONLY_VERIFIED = True
    return store, nodes, enc


# ---------------------------------------------------------------------------
# Benchmark 1: Encoding throughput
# ---------------------------------------------------------------------------

def bench_encoding(n: int = 500) -> dict:
    print(f"\n[1] Encoding throughput (n={n} states)")
    from sincor2.sinax.encoder import ProofState, HashingEncoder

    enc = HashingEncoder(dim=256)
    states = [
        ProofState(
            goal=f"⊢ P{i} ∧ Q{i}",
            hypotheses=[f"h : R{i}"],
            tactic_history=["intro", "apply"],
        )
        for i in range(n)
    ]

    _, elapsed = _timer(lambda: [enc.encode(s) for s in states])
    throughput = n / elapsed
    print(f"   Total time  : {_fmt_ms(elapsed)}")
    print(f"   Throughput  : {throughput:.0f} states/s")
    return {"name": "encoding", "n": n, "elapsed_s": elapsed, "throughput_per_s": throughput}


# ---------------------------------------------------------------------------
# Benchmark 2: K-NN retrieval latency
# ---------------------------------------------------------------------------

def bench_retrieval(n_index: int = 1000, k: int = 16, n_queries: int = 100) -> dict:
    print(f"\n[2] K-NN retrieval (index={n_index}, k={k}, queries={n_queries})")
    from sincor2.sinax.retrieval import RetrievalService
    from sincor2.sinax.graph_store import ProofGraphStore, ProofStateNode
    from sincor2.sinax.encoder import ProofState, HashingEncoder

    enc = HashingEncoder(dim=256)
    store = ProofGraphStore()
    svc = RetrievalService(store=store)
    for i in range(n_index):
        ps = ProofState(goal=f"⊢ Idx{i}")
        emb = enc.encode(ps)
        node = ProofStateNode(
            node_id=str(uuid.uuid4()), proof_state=ps, embedding=emb
        )
        store.add_node(node)
    _, rebuild_t = _timer(svc.rebuild_index)
    print(f"   Index build : {_fmt_ms(rebuild_t)}")

    queries = [enc.encode(ProofState(goal=f"⊢ Q{i}")) for i in range(n_queries)]
    _, total_t = _timer(lambda: [svc.query(q, k=k, use_cache=False) for q in queries])
    avg_latency = total_t / n_queries
    print(f"   Avg latency : {_fmt_ms(avg_latency)} per query")
    return {
        "name": "retrieval",
        "index_size": n_index,
        "k": k,
        "n_queries": n_queries,
        "index_build_s": rebuild_t,
        "avg_latency_s": avg_latency,
    }


# ---------------------------------------------------------------------------
# Benchmark 3: Geometric search step time
# ---------------------------------------------------------------------------

def bench_search(n_nodes: int = 200) -> dict:
    print(f"\n[3] Geometric search (graph={n_nodes} nodes)")
    from sincor2.sinax.search import GeometricSearchEngine
    from sincor2.sinax.retrieval import RetrievalService
    from sincor2.sinax.encoder import ProofState

    store, nodes, enc = _build_benchmark_graph(n_nodes)
    svc = RetrievalService(store=store)
    svc.rebuild_index()
    engine = GeometricSearchEngine(
        store=store, retrieval=svc, fallback_threshold=0.0, beam_width=8
    )
    query = ProofState(goal="⊢ Bench50", hypotheses=["h : P0"])

    n_runs = 50
    _, elapsed = _timer(lambda: [engine.search(query, max_steps=3) for _ in range(n_runs)])
    avg = elapsed / n_runs
    print(f"   Avg search  : {_fmt_ms(avg)} per call ({n_runs} runs)")
    return {
        "name": "geometric_search",
        "n_nodes": n_nodes,
        "n_runs": n_runs,
        "avg_latency_s": avg,
    }


# ---------------------------------------------------------------------------
# Benchmark 4: Curvature computation time
# ---------------------------------------------------------------------------

def bench_curvature(n_nodes: int = 200) -> dict:
    print(f"\n[4] Curvature computation (graph={n_nodes} nodes)")
    from sincor2.sinax.curvature import CurvatureAnalyzer
    from sincor2.sinax import config as cfg

    store, nodes, _ = _build_benchmark_graph(n_nodes)
    old_min = cfg.CURVATURE_MIN_OBS
    cfg.CURVATURE_MIN_OBS = 1
    analyzer = CurvatureAnalyzer(store=store)

    n_queries = 20
    _, elapsed = _timer(
        lambda: [
            analyzer.compute(nodes[i % len(nodes)].node_id, radius=2, use_cache=False)
            for i in range(n_queries)
        ]
    )
    cfg.CURVATURE_MIN_OBS = old_min
    avg = elapsed / n_queries
    print(f"   Avg compute : {_fmt_ms(avg)} per node ({n_queries} queries)")
    return {
        "name": "curvature",
        "n_nodes": n_nodes,
        "n_queries": n_queries,
        "avg_latency_s": avg,
    }


# ---------------------------------------------------------------------------
# Benchmark 5: Lemma discovery cycle time
# ---------------------------------------------------------------------------

def bench_lemma_discovery(n_nodes: int = 100) -> dict:
    print(f"\n[5] Lemma discovery cycle (graph={n_nodes} nodes, 20% failures)")
    from sincor2.sinax.lemma_discovery import LemmaDiscoveryEngine
    from sincor2.sinax.curvature import CurvatureAnalyzer
    from sincor2.sinax import config as cfg

    store, nodes, _ = _build_benchmark_graph(n_nodes, n_failures=20)
    old_min_obs = cfg.CURVATURE_MIN_OBS
    old_min_cluster = cfg.LEMMA_MIN_CLUSTER_SIZE
    cfg.CURVATURE_MIN_OBS = 1
    cfg.LEMMA_MIN_CLUSTER_SIZE = 1

    analyzer = CurvatureAnalyzer(store=store)
    engine = LemmaDiscoveryEngine(store=store, curvature_analyzer=analyzer, min_cluster_size=1)

    candidates, elapsed = _timer(engine.discover)
    cfg.CURVATURE_MIN_OBS = old_min_obs
    cfg.LEMMA_MIN_CLUSTER_SIZE = old_min_cluster

    print(f"   Discovery   : {_fmt_ms(elapsed)} (found {len(candidates)} candidates)")
    return {
        "name": "lemma_discovery",
        "n_nodes": n_nodes,
        "elapsed_s": elapsed,
        "candidates_found": len(candidates),
    }


# ---------------------------------------------------------------------------
# Baseline "AxiomSolver without SINAX" simulation
# ---------------------------------------------------------------------------

def bench_baseline_tactic_search(n_tactics: int = 1000) -> dict:
    """Simulate naive sequential tactic search as baseline comparison."""
    print(f"\n[BASELINE] Naive tactic search simulation (n_tactics={n_tactics})")
    tactics = [f"tactic_{i}" for i in range(n_tactics)]
    target = f"tactic_{n_tactics // 2}"

    def naive_search():
        for t in tactics:
            if t == target:
                return t
        return None

    _, elapsed = _timer(naive_search)
    print(f"   Found in    : {_fmt_ms(elapsed)}")
    return {"name": "baseline_tactic_search", "n_tactics": n_tactics, "elapsed_s": elapsed}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 64)
    print("SINAX BENCHMARK SUITE")
    print("=" * 64)

    results = []
    results.append(bench_baseline_tactic_search(n_tactics=1000))
    results.append(bench_encoding(n=500))
    results.append(bench_retrieval(n_index=500, k=16, n_queries=100))
    results.append(bench_search(n_nodes=150))
    results.append(bench_curvature(n_nodes=100))
    results.append(bench_lemma_discovery(n_nodes=80))

    print("\n" + "=" * 64)
    print("SUMMARY")
    print("=" * 64)
    for r in results:
        name = r["name"].replace("_", " ").title()
        if "avg_latency_s" in r:
            print(f"  {name:<35} avg latency: {_fmt_ms(r['avg_latency_s'])}")
        elif "throughput_per_s" in r:
            print(f"  {name:<35} throughput : {r['throughput_per_s']:.0f}/s")
        elif "elapsed_s" in r:
            print(f"  {name:<35} elapsed    : {_fmt_ms(r['elapsed_s'])}")
    print("=" * 64)

    out_path = "/tmp/sinax_benchmark_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
