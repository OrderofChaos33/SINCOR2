"""Frozen API contracts for the memory sub-engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .vector_retrieval_engine import QuerySpec, RankedResult, WarmSegment


@runtime_checkable
class QueryPreFilter(Protocol):
    def __call__(self, spec: QuerySpec) -> list[RankedResult]:
        ...


@runtime_checkable
class InsertSnapshotDelta(Protocol):
    def __call__(self, record) -> None:
        ...


@runtime_checkable
class CompactWarmSegment(Protocol):
    def __call__(self) -> WarmSegment:
        ...


@runtime_checkable
class SwapColdEpoch(Protocol):
    def __call__(self, max_pause_ms: float = 5.0) -> str:
        ...


@dataclass(frozen=True)
class MemoryContracts:
    query_pre_filter: str = "QueryPreFilter(QuerySpec)->list[RankedResult]"
    insert_snapshot_delta: str = "InsertSnapshotDelta(VectorRecord)->None"
    compact_warm_segment: str = "CompactWarmSegment()->WarmSegment"
    swap_cold_epoch: str = "SwapColdEpoch(max_pause_ms=5.0)->str"
