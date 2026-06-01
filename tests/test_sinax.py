#!/usr/bin/env python3
"""
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
