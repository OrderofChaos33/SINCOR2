# SINAX — Geometric Proof Navigation Layer

SINAX augments AxiomSolver with a geometric navigation layer that learns the
topology of proof space and reuses verified trajectories to speed up future
proof searches.

```
SINC (orchestration)
  └─ AxiomSolver (formal prover / Lean verifier)
       └─ SINAX (geometric navigation augmentation)
            └─ A2A-agnostic integration adapters
```

**Core contract**: SINAX *proposes*, the verifier *certifies*.  No proof step
is accepted without confirmation from Lean (or the active verifier).

---

## Package layout

```
src/sincor2/sinax/
├── __init__.py          # re-exports all public symbols
├── config.py            # runtime parameters (env-var overridable)
├── encoder.py           # Phase 1 — Proof State Encoder
├── graph_store.py       # Phase 2 — Proof Graph Store
├── retrieval.py         # Phase 3 — Embedding Retrieval Service
├── search.py            # Phase 4 — Geometric Search Engine
├── curvature.py         # Phase 5 — Curvature Analyzer
├── lemma_discovery.py   # Phase 6 — Lemma Discovery Engine
├── integration.py       # Phase 7a — A2A-agnostic Integration Layer
└── visualization.py     # Phase 7b — Visualization Layer
```

---

## Configuration

All parameters can be overridden with environment variables.  Defaults are
suitable for development; tune them for production.

| Variable | Default | Description |
|---|---|---|
| `SINAX_ENABLED` | `true` | Master on/off switch |
| `SINAX_MODE` | `analytics` | `analytics` \| `suggest` \| `active` |
| `SINAX_EMBEDDING_DIM` | `256` | Encoder output dimension |
| `SINAX_ENCODER_VERSION` | `hashing-v1` | Active encoder key |
| `SINAX_GRAPH_MAX_NODES` | `100000` | LRU eviction ceiling |
| `SINAX_GRAPH_WRITE_ONLY_VERIFIED` | `true` | Reject unverified edges |
| `SINAX_KNN_K` | `16` | Neighbours returned per query |
| `SINAX_BEAM_WIDTH` | `8` | Beam-search width |
| `SINAX_MAX_SEARCH_STEPS` | `5` | Beam-search depth |
| `SINAX_FALLBACK_SIMILARITY_THRESHOLD` | `0.1` | Minimum sim to trust SINAX |
| `SINAX_EXPLORATION_WEIGHT` | `0.3` | Exploration/exploitation blend |
| `SINAX_CURVATURE_MIN_OBS` | `3` | Min edges for curvature score |
| `SINAX_LEMMA_MIN_CLUSTER_SIZE` | `3` | Min failures to form a cluster |

Staged rollout:

| `SINAX_MODE` | Behaviour |
|---|---|
| `analytics` | Records proof states and transitions; never influences the prover |
| `suggest` | Provides candidate tactics to AxiomSolver but does not override |
| `active` | Drives proof search; falls back to AxiomSolver on low confidence |

---

## Module contracts

### Phase 1 — Proof State Encoder (`encoder.py`)

Converts a `ProofState` into a fixed-length `Embedding`.

```python
from sincor2.sinax import ProofState, get_encoder

state = ProofState(
    goal="⊢ n + 0 = n",
    hypotheses=["n : ℕ"],
    tactic_history=["induction n"],
)
enc = get_encoder()          # returns the registered/configured encoder
emb = enc.encode(state)      # → Embedding(vector, dim, model_version, proof_state_hash)
emb_norm = emb.normalised()  # unit-norm copy for cosine comparisons
```

**Contrastive training hook**

```python
from sincor2.sinax import contrastive_loss
import numpy as np

anchors   = np.random.randn(8, 256).astype("float32")
positives = anchors + np.random.randn(8, 256).astype("float32") * 0.01
loss = contrastive_loss(anchors, positives, temperature=0.07)
```

**Custom encoder registration**

```python
from sincor2.sinax import BaseEncoder, register_encoder, Embedding

class MyNeuralEncoder(BaseEncoder):
    version = "neural-v1"
    def encode(self, state): ...
    def batch_encode(self, states): ...

register_encoder("neural-v1", MyNeuralEncoder())
```

---

### Phase 2 — Proof Graph Store (`graph_store.py`)

Thread-safe, LRU-evicting store of verified proof transitions.

```python
from sincor2.sinax import (
    get_store, ProofStateNode, TacticEdge, VerificationResult
)

store = get_store()   # process singleton

node = ProofStateNode(node_id="abc", proof_state=state, embedding=emb, depth=3)
store.add_node(node)

edge = TacticEdge(
    edge_id="e1",
    source_id="abc", target_id="def",
    tactic="simp",
    verification_result=VerificationResult.VERIFIED,
    cost=1.0,
)
store.add_edge(edge)   # rejected if target unknown or result != VERIFIED
                       # (when SINAX_GRAPH_WRITE_ONLY_VERIFIED=true)

neighbours = store.nearest_neighbours(query_emb, k=8)   # [(node, sim), ...]
path       = store.reconstruct_path("abc", "xyz")       # [(node, edge|None), ...]
```

**Node schema**

| Field | Type | Description |
|---|---|---|
| `node_id` | `str` | UUID |
| `proof_state` | `ProofState` | Canonical state |
| `embedding` | `Embedding` | Dense vector |
| `depth` | `int` | Proof depth |
| `success` | `bool` | Did a proof complete from here? |
| `metadata` | `dict` | Arbitrary key-value pairs |

**Edge schema**

| Field | Type | Description |
|---|---|---|
| `edge_id` | `str` | UUID |
| `source_id` / `target_id` | `str` | Node references |
| `tactic` | `str` | Lean/Mathlib tactic string |
| `verification_result` | `VerificationResult` | `VERIFIED \| FAILED \| PENDING` |
| `cost` | `float` | Step cost (default 1.0) |
| `timestamp` | `float` | Unix epoch |

---

### Phase 3 — Embedding Retrieval Service (`retrieval.py`)

K-NN lookup over the graph's embedding matrix.

```python
from sincor2.sinax import get_retrieval_service

svc = get_retrieval_service()
svc.rebuild_index()                         # full rebuild from store
svc.add_node(node)                          # incremental update

results = svc.query(emb_or_state, k=16)     # [(node, similarity), ...]
stats   = svc.stats()                       # {"index_size": N, "cache_size": M}
svc.invalidate_cache()
```

---

### Phase 4 — Geometric Search Engine (`search.py`)

Latent-space beam search over the proof graph.

```python
from sincor2.sinax import get_search_engine

engine = get_search_engine()

candidates = engine.search(current_state, goal_state=target_state)
# Returns None  → fall back to AxiomSolver's native tactic search
# Returns list  → SearchCandidate(tactic, source_node, target_node,
#                                 similarity_to_target, score)

tactics = engine.generate_tactics(current_state, top_k=5)   # ["simp", "ring", ...]
region  = engine.predict_target_region(goal_state)           # Embedding
```

**Fallback contract**: when `search()` returns `None`, the caller **must**
invoke AxiomSolver's native tactic search.  SINAX never blocks the prover.

---

### Phase 5 — Curvature Analyzer (`curvature.py`)

Measures local complexity of a graph region.

```python
from sincor2.sinax import get_curvature_analyzer

analyzer = get_curvature_analyzer()
region   = analyzer.compute(node_id, radius=2)
# Returns None when fewer than SINAX_CURVATURE_MIN_OBS edges are observed.

# RegionCurvature fields:
#   branching_factor   — average out-degree
#   failure_density    — fraction of failed edges
#   avg_depth          — mean node depth
#   success_frequency  — fraction of successful nodes
#   score              — composite in [0, 1]; higher = harder region
```

---

### Phase 6 — Lemma Discovery Engine (`lemma_discovery.py`)

Identifies proof bottlenecks and proposes bridging lemmas.

```python
from sincor2.sinax import get_lemma_engine

engine     = get_lemma_engine()
candidates = engine.discover()
# Returns list[LemmaCandidate] sorted by score descending.
# Every candidate has already been routed through Lean/verifier;
# only VERIFIED or PENDING candidates are returned.

# LemmaCandidate fields:
#   lemma_id, statement, cluster_nodes, verification_result, score, metadata
```

**Safety invariant**: `PENDING` candidates are not used for active proof
guidance until re-verified on the next discover cycle.

---

### Phase 7a — A2A-Agnostic Integration Layer (`integration.py`)

Canonical message objects and protocol adapters.

**Canonical objects**

| Object | Key fields |
|---|---|
| `Task` | `task_id`, `task_type`, `payload`, `status` |
| `ProofStateMessage` | `proof_state`, `embedding_vector` |
| `LemmaMessage` | `statement`, `verification_result` |
| `ConjectureMessage` | `statement`, `context` |
| `VerificationResultMessage` | `result`, `verifier`, `proof_term` |
| `Trajectory` | `nodes`, `edges`, `metadata` |

**Adapter interface**

```python
from sincor2.sinax.integration import BaseAdapter, Task

class MyAdapter(BaseAdapter):
    name = "my-protocol"
    def send(self, task: Task) -> Task: ...
    def receive(self) -> Task: ...
    def status(self) -> dict: ...
```

**Built-in adapters**: `LocalAdapter`, `A2AAdapter`, `MCPAdapter`, `RESTAdapter`.

**Router**

```python
from sincor2.sinax import get_router

router = get_router()
result = router.dispatch(task)   # routes by task.task_type → handler
```

---

### Phase 7b — Visualization Layer (`visualization.py`)

```python
from sincor2.sinax import get_dashboard

dash = get_dashboard()
summary = dash.summary()
# {
#   "graph":     {"nodes": N, "edges": E, ...},
#   "hotspots":  [...],           # high-curvature nodes
#   "lemmas":    [...],           # discovered candidates
# }
```

Individual renderers:

| Class | Method | Output |
|---|---|---|
| `GraphVisualizer` | `render(max_nodes)` | `{"nodes": [...], "edges": [...]}` |
| `TrajectoryVisualizer` | `render(start_id, end_id)` | path steps or `{"found": false}` |
| `CurvatureHeatmap` | `render(top_n)` | sorted curvature scores |
| `LemmaReport` | `render()` | verified + pending lemma lists |

---

## Testing

```bash
# Unit + integration tests (all modules)
PYTHONPATH=src:src/sincor2 python -m pytest tests/test_sinax.py -v

# Benchmark suite (throughput and latency)
PYTHONPATH=src:src/sincor2 python tests/benchmark_sinax.py
```

Reference benchmark results (Python 3.12, numpy 1.x, no GPU):

| Metric | Value |
|---|---|
| Encoding throughput | ~14 000 states/s |
| K-NN retrieval latency (index=500, k=16) | ~0.07 ms |
| Geometric search step (150-node graph) | ~0.14 ms |
| Curvature computation | ~0.02 ms |
| Lemma discovery cycle (80 nodes) | ~12 ms |

---

## Ops notes

### Model versioning

The active encoder is selected by `SINAX_ENCODER_VERSION`.  The version tag is
embedded in every `Embedding`, so nodes from different encoder versions remain
distinguishable in the graph store.  When you upgrade the encoder, rebuild the
index and run a migration to re-encode existing nodes.

### Graph retention

The graph store evicts the oldest node (LRU) when `len(nodes) >=
SINAX_GRAPH_MAX_NODES`.  Eviction removes the node **and all its edges**.
For long-running deployments, periodically export the graph to persistent
storage and reload on startup.

### Safety constraints

1. `SINAX_GRAPH_WRITE_ONLY_VERIFIED=true` (default) — no unverified edge can
   enter the graph.
2. In `analytics` mode SINAX is read-only; it never influences AxiomSolver.
3. In `suggest` / `active` modes, every tactic proposed by SINAX is still
   submitted to Lean; SINAX does **not** skip formal verification.
4. Lemma candidates remain `PENDING` until Lean confirms them.
5. `search()` returning `None` is a first-class fallback path — callers must
   handle it by delegating to AxiomSolver.

### Dependency requirements

SINAX requires only `numpy` (already in `requirements.txt`).  No external ML
frameworks, model downloads, or GPU are needed for the default `HashingEncoder`.
A neural encoder can be plugged in by subclassing `BaseEncoder` and calling
`register_encoder()`.
