"""Three-tier vector retrieval engine with deterministic epoch swaps.

Implements:
- Hot path snapshot delta writes + tombstones
- Warm path asynchronous compaction with exponential temporal decay
- Cold path epoch staging/commit with immutable Merkle commitments
- Deterministic query routing: bitmap pre-filter -> ANN + lexical merge
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

import numpy as np


def _now() -> float:
    return time.time()


def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    buf = []
    token = []
    for ch in text.lower():
        if ch.isalnum():
            token.append(ch)
        elif token:
            buf.append("".join(token))
            token = []
    if token:
        buf.append("".join(token))
    return buf


@dataclass(frozen=True)
class VectorRecord:
    node_id: str
    vector: np.ndarray
    text: str
    attributes: Mapping[str, str] = field(default_factory=dict)
    capabilities: Set[str] = field(default_factory=set)
    created_at: float = field(default_factory=_now)
    weight: float = 1.0
    adjacency: Tuple[str, ...] = ()
    session_key_expires_at: Optional[float] = None
    task_state_expires_at: Optional[float] = None

    def payload_hash(self) -> str:
        payload = {
            "node_id": self.node_id,
            "vector": [float(x) for x in self.vector.tolist()],
            "text": self.text,
            "attributes": dict(sorted((self.attributes or {}).items())),
            "capabilities": sorted(self.capabilities or set()),
            "created_at": float(self.created_at),
            "weight": float(self.weight),
            "adjacency": sorted(str(x) for x in self.adjacency),
            "session_key_expires_at": self.session_key_expires_at,
            "task_state_expires_at": self.task_state_expires_at,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass
class QuerySpec:
    query_vector: np.ndarray
    query_text: str
    required_attributes: Mapping[str, Union[str, Sequence[str]]] = field(default_factory=dict)
    required_capabilities: Set[str] = field(default_factory=set)
    k: int = 10
    epoch_id: Optional[str] = None


@dataclass
class RankedResult:
    node_id: str
    score: float
    vector_score: float
    lexical_score: float
    epoch_id: str


class SnapshotDeltaBuffer:
    """Thread-safe hot-path writes and tombstones.

    Incoming writes are only appended to the active delta map; base segments are immutable.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._write_gate = threading.Condition(self._lock)
        self._writes_paused = False

        self._delta: Dict[str, VectorRecord] = {}
        self._tombstones: Set[str] = set()
        self._rewire_one_hop: Dict[str, Set[str]] = defaultdict(set)

    def upsert(self, rec: VectorRecord) -> None:
        with self._lock:
            while self._writes_paused:
                self._write_gate.wait(timeout=0.001)
            self._delta[rec.node_id] = rec
            self._tombstones.discard(rec.node_id)

    def tombstone(self, node_id: str, rewire_targets: Optional[Iterable[str]] = None) -> None:
        with self._lock:
            while self._writes_paused:
                self._write_gate.wait(timeout=0.001)
            self._delta.pop(node_id, None)
            self._tombstones.add(node_id)
            if rewire_targets:
                self._rewire_one_hop[node_id].update(set(rewire_targets))

    def pause_writes(self) -> None:
        with self._lock:
            self._writes_paused = True

    def resume_writes(self) -> None:
        with self._lock:
            self._writes_paused = False
            self._write_gate.notify_all()

    def snapshot_and_clear(self) -> Tuple[Dict[str, VectorRecord], Set[str], Dict[str, Set[str]]]:
        with self._lock:
            delta = dict(self._delta)
            tombstones = set(self._tombstones)
            rewires = {k: set(v) for k, v in self._rewire_one_hop.items()}
            self._delta.clear()
            self._tombstones.clear()
            self._rewire_one_hop.clear()
            return delta, tombstones, rewires

    def read_delta(self) -> Tuple[Dict[str, VectorRecord], Set[str]]:
        with self._lock:
            return dict(self._delta), set(self._tombstones)


@dataclass(frozen=True)
class WarmSegment:
    records: Mapping[str, VectorRecord]
    created_at: float


class WarmCompactionWorker:
    """Background warm compactor applying temporal decay w = exp(-lambda * t)."""

    def __init__(
        self,
        delta: SnapshotDeltaBuffer,
        decay_lambda: float = 1e-4,
        epsilon: float = 0.05,
        interval_seconds: float = 5.0,
    ) -> None:
        self._delta = delta
        self._decay_lambda = decay_lambda
        self._epsilon = epsilon
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._warm = WarmSegment(records={}, created_at=_now())

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="sincor-warm-compact")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def compact_once(self) -> WarmSegment:
        now = _now()
        delta_records, tombstones, _ = self._delta.snapshot_and_clear()
        with self._lock:
            merged = dict(self._warm.records)
            for tid in tombstones:
                merged.pop(tid, None)
            merged.update(delta_records)

            pruned = {}
            for node_id, rec in merged.items():
                if rec.session_key_expires_at is not None and rec.session_key_expires_at <= now:
                    continue
                if rec.task_state_expires_at is not None and rec.task_state_expires_at <= now:
                    continue
                age_seconds = max(0.0, now - rec.created_at)
                decayed_weight = rec.weight * math.exp(-self._decay_lambda * age_seconds)
                if decayed_weight < self._epsilon:
                    continue
                pruned[node_id] = VectorRecord(
                    node_id=rec.node_id,
                    vector=rec.vector,
                    text=rec.text,
                    attributes=rec.attributes,
                    capabilities=set(rec.capabilities),
                    created_at=rec.created_at,
                    weight=decayed_weight,
                    adjacency=tuple(sorted(rec.adjacency)),
                    session_key_expires_at=rec.session_key_expires_at,
                    task_state_expires_at=rec.task_state_expires_at,
                )

            self._warm = WarmSegment(records=pruned, created_at=now)
            return self._warm

    def current_segment(self) -> WarmSegment:
        with self._lock:
            return self._warm

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            self.compact_once()


@dataclass(frozen=True)
class EpochSegment:
    epoch_id: str
    model_version: str
    merkle_root: str
    created_at: float
    records: Mapping[str, VectorRecord]
    manifest_hash: str
    matrix: np.ndarray
    row_ids: Tuple[str, ...]
    norm_matrix: np.ndarray
    lexical_postings: Mapping[str, Tuple[str, ...]]
    doc_len: Mapping[str, int]
    attr_bitmap: Mapping[str, Mapping[str, Set[str]]]
    cap_bitmap: Mapping[str, Set[str]]
    attr_bitsets: Mapping[str, Mapping[str, int]]
    cap_bitsets: Mapping[str, int]
    id_position: Mapping[str, int]
    adjacency_manifest: Mapping[str, Tuple[str, ...]]


class EpochBuilder:
    """Build immutable cold segments with deterministic commitments."""

    @staticmethod
    def orthogonal_procrustes(source: np.ndarray, target: np.ndarray) -> np.ndarray:
        m = source.T @ target
        u, _, vt = np.linalg.svd(m, full_matrices=False)
        return u @ vt

    @staticmethod
    def align_vectors(vectors: np.ndarray, rotation: np.ndarray) -> np.ndarray:
        return vectors @ rotation

    def build(self, records: Mapping[str, VectorRecord], model_version: str, parent_epoch: Optional[str] = None) -> EpochSegment:
        ordered_ids = tuple(sorted(records.keys()))
        ordered_records = {node_id: records[node_id] for node_id in ordered_ids}

        adjacency_manifest: Dict[str, Tuple[str, ...]] = {
            node_id: tuple(sorted(str(x) for x in ordered_records[node_id].adjacency))
            for node_id in ordered_ids
        }
        leaves = [
            hashlib.sha256(
                (
                    ordered_records[node_id].payload_hash()
                    + "|"
                    + hashlib.sha256(",".join(adjacency_manifest[node_id]).encode("utf-8")).hexdigest()
                ).encode("utf-8")
            ).hexdigest()
            for node_id in ordered_ids
        ]
        merkle_root = self._merkle_root(leaves)
        manifest = {
            "ordered_ids": ordered_ids,
            "model_version": model_version,
            "parent_epoch": parent_epoch,
            "record_count": len(ordered_ids),
            "merkle_root": merkle_root,
            "adjacency_manifest_sha256": hashlib.sha256(
                json.dumps(adjacency_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }
        manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        manifest_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
        epoch_id = f"E_{manifest_hash[:16]}"

        matrix = np.stack([ordered_records[node_id].vector for node_id in ordered_ids]) if ordered_ids else np.zeros((0, 0), dtype=np.float64)
        matrix = matrix.astype(np.float64)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True) if matrix.size else np.zeros((0, 1), dtype=np.float64)
        norms[norms == 0] = 1.0
        norm_matrix = matrix / norms if matrix.size else matrix

        lexical_postings: Dict[str, List[str]] = defaultdict(list)
        doc_len: Dict[str, int] = {}
        attr_bitmap: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
        cap_bitmap: Dict[str, Set[str]] = defaultdict(set)
        id_position: Dict[str, int] = {node_id: idx for idx, node_id in enumerate(ordered_ids)}
        attr_bitsets: Dict[str, Dict[str, int]] = defaultdict(dict)
        cap_bitsets: Dict[str, int] = {}

        for node_id in ordered_ids:
            rec = ordered_records[node_id]
            toks = _tokenize(rec.text)
            doc_len[node_id] = len(toks)
            for tok in toks:
                lexical_postings[tok].append(node_id)
            for key, val in rec.attributes.items():
                attr_bitmap[key][val].add(node_id)
            for cap in rec.capabilities:
                cap_bitmap[cap].add(node_id)

        immutable_postings = {k: tuple(sorted(v)) for k, v in lexical_postings.items()}
        immutable_attr = {k: {vv: set(ids) for vv, ids in vm.items()} for k, vm in attr_bitmap.items()}
        immutable_cap = {k: set(v) for k, v in cap_bitmap.items()}
        for key, value_map in immutable_attr.items():
            for val, ids in value_map.items():
                mask = 0
                for node_id in ids:
                    mask |= 1 << id_position[node_id]
                attr_bitsets[key][val] = mask
        for cap, ids in immutable_cap.items():
            mask = 0
            for node_id in ids:
                mask |= 1 << id_position[node_id]
            cap_bitsets[cap] = mask

        return EpochSegment(
            epoch_id=epoch_id,
            model_version=model_version,
            merkle_root=merkle_root,
            created_at=_now(),
            records=ordered_records,
            manifest_hash=manifest_hash,
            matrix=matrix,
            row_ids=ordered_ids,
            norm_matrix=norm_matrix,
            lexical_postings=immutable_postings,
            doc_len=doc_len,
            attr_bitmap=immutable_attr,
            cap_bitmap=immutable_cap,
            attr_bitsets={k: dict(v) for k, v in attr_bitsets.items()},
            cap_bitsets=cap_bitsets,
            id_position=id_position,
            adjacency_manifest=adjacency_manifest,
        )

    def _merkle_root(self, leaves: Sequence[str]) -> str:
        if not leaves:
            return hashlib.sha256(b"").hexdigest()
        level = [bytes.fromhex(x) for x in leaves]
        while len(level) > 1:
            nxt = []
            for i in range(0, len(level), 2):
                left = level[i]
                right = level[i + 1] if i + 1 < len(level) else left
                nxt.append(hashlib.sha256(left + right).digest())
            level = nxt
        return level[0].hex()


class AtomicSwapController:
    """Two-phase atomic epoch swap controller with write pause and reader drain."""

    def __init__(self, delta: SnapshotDeltaBuffer, memory_headroom_factor: float = 2.0) -> None:
        self._delta = delta
        self._memory_headroom_factor = memory_headroom_factor
        self._lock = threading.RLock()
        self._reader_cond = threading.Condition(self._lock)
        self._active_readers = 0

        self._active_epoch: Optional[EpochSegment] = None
        self._staged_epoch: Optional[EpochSegment] = None
        self._epochs_by_id: Dict[str, EpochSegment] = {}
        self.last_pause_ms: float = 0.0
        self.total_pause_ms: float = 0.0
        self.pause_events: int = 0

    def stage_shadow_epoch(self, epoch: EpochSegment) -> None:
        with self._lock:
            if self._active_epoch is not None:
                projected = self._estimate_bytes(self._active_epoch) + self._estimate_bytes(epoch)
                peak = max(1, self._estimate_bytes(self._active_epoch))
                if projected > int(self._memory_headroom_factor * peak):
                    raise MemoryError("insufficient RAM headroom for staged epoch")
            self._staged_epoch = epoch

    def drain_and_cutover(self, max_pause_ms: float = 5.0) -> str:
        start = _now()
        self._delta.pause_writes()
        try:
            delta_records, tombstones, _ = self._delta.snapshot_and_clear()
            with self._lock:
                if self._staged_epoch is None:
                    raise RuntimeError("no staged epoch")
                if self._active_epoch is not None:
                    merged = dict(self._active_epoch.records)
                    for tid in tombstones:
                        merged.pop(tid, None)
                    merged.update(delta_records)
                else:
                    merged = dict(self._staged_epoch.records)
                    for tid in tombstones:
                        merged.pop(tid, None)
                    merged.update(delta_records)

                builder = EpochBuilder()
                next_epoch = builder.build(
                    merged,
                    model_version=self._staged_epoch.model_version,
                    parent_epoch=self._active_epoch.epoch_id if self._active_epoch else None,
                )
                self._active_epoch = next_epoch
                self._epochs_by_id[next_epoch.epoch_id] = next_epoch
                self._staged_epoch = None

                while self._active_readers > 0:
                    self._reader_cond.wait(timeout=0.001)

            elapsed_ms = (_now() - start) * 1000.0
            self.last_pause_ms = elapsed_ms
            self.total_pause_ms += elapsed_ms
            self.pause_events += 1
            if elapsed_ms > max_pause_ms:
                # non-fatal: exposed as runtime observability signal
                pass
            return self._active_epoch.epoch_id
        finally:
            self._delta.resume_writes()

    def active_epoch(self) -> Optional[EpochSegment]:
        with self._lock:
            return self._active_epoch

    def get_epoch(self, epoch_id: Optional[str]) -> Optional[EpochSegment]:
        with self._lock:
            if epoch_id:
                return self._epochs_by_id.get(epoch_id)
            if self._active_epoch is None:
                return None
            return self._active_epoch

    def begin_read(self) -> None:
        with self._lock:
            self._active_readers += 1

    def end_read(self) -> None:
        with self._lock:
            self._active_readers = max(0, self._active_readers - 1)
            if self._active_readers == 0:
                self._reader_cond.notify_all()

    def _estimate_bytes(self, epoch: EpochSegment) -> int:
        matrix_bytes = int(epoch.matrix.nbytes + epoch.norm_matrix.nbytes)
        meta_bytes = int(len(epoch.records) * 1024)
        return matrix_bytes + meta_bytes


class QueryRouter:
    """Deterministic retrieval pipeline with mandatory lexical fallback floor."""

    def __init__(self, controller: AtomicSwapController, lexical_floor_k: int = 3) -> None:
        self._controller = controller
        self._lexical_floor_k = lexical_floor_k

    def route(self, spec: QuerySpec) -> List[RankedResult]:
        self._controller.begin_read()
        try:
            epoch = self._controller.get_epoch(spec.epoch_id)
            if epoch is None:
                return []

            candidates = self._prefilter(epoch, spec.required_attributes, spec.required_capabilities)
            if not candidates:
                return []

            vector_scores = self._ann_search(epoch, spec.query_vector, candidates, spec.k)
            lexical_scores = self._bm25(epoch, spec.query_text, candidates)
            merged = self._hybrid_merge(epoch, vector_scores, lexical_scores, spec.k)
            return merged
        finally:
            self._controller.end_read()

    def _prefilter(
        self,
        epoch: EpochSegment,
        attrs: Mapping[str, Union[str, Sequence[str]]],
        caps: Set[str],
    ) -> Set[str]:
        universe_mask = (1 << len(epoch.row_ids)) - 1 if epoch.row_ids else 0

        for key, val in attrs.items():
            key_map = epoch.attr_bitsets.get(key)
            if not key_map:
                return set()
            if isinstance(val, str):
                vals = [val]
            else:
                vals = [str(v) for v in val]
            attr_mask = 0
            for vv in vals:
                attr_mask |= int(key_map.get(vv, 0))
            if attr_mask == 0:
                return set()
            universe_mask &= attr_mask
            if universe_mask == 0:
                return set()

        for cap in caps:
            cap_mask = int(epoch.cap_bitsets.get(cap, 0))
            if cap_mask == 0:
                return set()
            universe_mask &= cap_mask
            if universe_mask == 0:
                return set()

        if universe_mask == 0:
            return set()
        candidates: Set[str] = set()
        for idx, node_id in enumerate(epoch.row_ids):
            if (universe_mask >> idx) & 1:
                candidates.add(node_id)
        return candidates

    def _ann_search(self, epoch: EpochSegment, query_vector: np.ndarray, candidates: Set[str], k: int) -> Dict[str, float]:
        if epoch.norm_matrix.size == 0:
            return {}
        q = query_vector.astype(np.float64)
        qn = np.linalg.norm(q) or 1.0
        q = q / qn

        allowed = np.array([nid in candidates for nid in epoch.row_ids], dtype=bool)
        if not np.any(allowed):
            return {}

        sims = epoch.norm_matrix @ q
        sims[~allowed] = -np.inf
        k_eff = min(k, int(np.sum(allowed)))
        if k_eff <= 0:
            return {}

        top_idx = np.argpartition(sims, -k_eff)[-k_eff:]
        top_idx = top_idx[np.argsort(-sims[top_idx])]
        return {
            epoch.row_ids[int(i)]: float(sims[int(i)])
            for i in top_idx
            if np.isfinite(sims[int(i)])
        }

    def _bm25(self, epoch: EpochSegment, query_text: str, candidates: Set[str]) -> Dict[str, float]:
        tokens = _tokenize(query_text)
        if not tokens:
            return {}

        n_docs = max(1, len(epoch.records))
        avg_dl = (sum(epoch.doc_len.values()) / n_docs) if n_docs else 0.0
        k1 = 1.2
        b = 0.75
        scores: Dict[str, float] = defaultdict(float)

        for tok in tokens:
            posting = epoch.lexical_postings.get(tok)
            if not posting:
                continue
            doc_ids = [d for d in posting if d in candidates]
            if not doc_ids:
                continue
            df = len(set(posting))
            idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))

            for node_id in doc_ids:
                tf = _tokenize(epoch.records[node_id].text).count(tok)
                dl = max(1, epoch.doc_len.get(node_id, 1))
                denom = tf + k1 * (1.0 - b + b * (dl / max(avg_dl, 1e-9)))
                scores[node_id] += idf * ((tf * (k1 + 1.0)) / max(denom, 1e-9))

        return dict(scores)

    def _hybrid_merge(
        self,
        epoch: EpochSegment,
        vector_scores: Dict[str, float],
        lexical_scores: Dict[str, float],
        k: int,
    ) -> List[RankedResult]:
        merged_ids = set(vector_scores.keys()) | set(lexical_scores.keys())

        top_lex = sorted(lexical_scores.items(), key=lambda x: x[1], reverse=True)[: self._lexical_floor_k]
        merged_ids.update([node_id for node_id, _ in top_lex])

        if not merged_ids:
            return []

        v_max = max(vector_scores.values()) if vector_scores else 1.0
        v_min = min(vector_scores.values()) if vector_scores else 0.0
        l_max = max(lexical_scores.values()) if lexical_scores else 1.0
        l_min = min(lexical_scores.values()) if lexical_scores else 0.0

        def norm(val: float, lo: float, hi: float) -> float:
            if hi <= lo:
                return 0.0
            return (val - lo) / (hi - lo)

        results: List[RankedResult] = []
        for node_id in sorted(merged_ids):
            v = vector_scores.get(node_id, float("-inf"))
            l = lexical_scores.get(node_id, 0.0)
            v_norm = 0.0 if not np.isfinite(v) else norm(v, v_min, v_max)
            l_norm = norm(l, l_min, l_max)
            weight_norm = max(0.0, min(1.0, float(epoch.records[node_id].weight)))
            combined = 0.6 * v_norm + 0.2 * l_norm + 0.2 * weight_norm
            results.append(
                RankedResult(
                    node_id=node_id,
                    score=combined,
                    vector_score=0.0 if not np.isfinite(v) else v,
                    lexical_score=l,
                    epoch_id=epoch.epoch_id,
                )
            )

        results.sort(key=lambda r: (-r.score, r.node_id))
        return results[:k]


class ThreeTierVectorEngine:
    """Facade for hot/warm/cold lifecycle and deterministic querying."""

    def __init__(self, model_version: str = "v1", decay_lambda: float = 1e-4, epsilon: float = 0.05) -> None:
        self.delta = SnapshotDeltaBuffer()
        self.compactor = WarmCompactionWorker(self.delta, decay_lambda=decay_lambda, epsilon=epsilon)
        self.builder = EpochBuilder()
        self.swap = AtomicSwapController(self.delta, memory_headroom_factor=2.0)
        self.router = QueryRouter(self.swap)
        self.model_version = model_version

    def start(self) -> None:
        self.compactor.start()

    def stop(self) -> None:
        self.compactor.stop()

    def write(self, rec: VectorRecord) -> None:
        self.delta.upsert(rec)

    def delete(self, node_id: str, rewire_targets: Optional[Iterable[str]] = None) -> None:
        self.delta.tombstone(node_id, rewire_targets=rewire_targets)

    def compact_warm(self) -> WarmSegment:
        return self.compactor.compact_once()

    def stage_epoch_from_warm(self, model_version: Optional[str] = None) -> EpochSegment:
        warm = self.compactor.current_segment()
        epoch = self.builder.build(
            records=warm.records,
            model_version=model_version or self.model_version,
            parent_epoch=self.swap.active_epoch().epoch_id if self.swap.active_epoch() else None,
        )
        self.swap.stage_shadow_epoch(epoch)
        return epoch

    def cutover(self, max_pause_ms: float = 5.0) -> str:
        return self.swap.drain_and_cutover(max_pause_ms=max_pause_ms)

    def query(self, spec: QuerySpec) -> List[RankedResult]:
        return self.router.route(spec)

    # Frozen contract aliases
    def QueryPreFilter(self, spec: QuerySpec) -> List[RankedResult]:  # noqa: N802
        return self.query(spec)

    def InsertSnapshotDelta(self, rec: VectorRecord) -> None:  # noqa: N802
        self.write(rec)

    def CompactWarmSegment(self) -> WarmSegment:  # noqa: N802
        return self.compact_warm()

    def SwapColdEpoch(self, max_pause_ms: float = 5.0) -> str:  # noqa: N802
        return self.cutover(max_pause_ms=max_pause_ms)
