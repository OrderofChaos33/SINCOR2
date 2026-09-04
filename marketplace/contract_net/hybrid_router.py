"""Contract-Net integration for epoch-bound SINAX hybrid retrieval."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

try:  # pragma: no cover - optional dependency in minimal runtimes
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]

try:  # pragma: no cover - optional dependency in minimal runtimes
    from sincor2.sinax.vector_retrieval_engine import (
        QuerySpec,
        RankedResult,
        ThreeTierVectorEngine,
        VectorRecord,
    )
except Exception:  # pragma: no cover
    QuerySpec = None  # type: ignore[assignment]
    RankedResult = None  # type: ignore[assignment]
    ThreeTierVectorEngine = None  # type: ignore[assignment]
    VectorRecord = None  # type: ignore[assignment]

from .types import AgentProfile, TaskSpec
from .vectors import embed_tokens


def _norm_token(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


@dataclass(frozen=True)
class EpochBinding:
    epoch_id: str
    merkle_root: str


@dataclass(frozen=True)
class RoutedAgent:
    agent: AgentProfile
    score: float
    vector_score: float
    lexical_score: float
    epoch_id: str


class TaskAuctionMemoryRouter:
    """Hard-pre-filter + hybrid rank for Contract-Net agent selection."""

    def __init__(self, engine: Optional[ThreeTierVectorEngine] = None, *, bid_state_ttl_seconds: int = 300) -> None:
        if np is None or ThreeTierVectorEngine is None or QuerySpec is None or VectorRecord is None:
            raise RuntimeError("TaskAuctionMemoryRouter requires numpy and sinax.vector_retrieval_engine")
        self.engine = engine or ThreeTierVectorEngine(model_version="cn-v1", decay_lambda=2e-4, epsilon=0.01)
        self.bid_state_ttl_seconds = int(max(1, bid_state_ttl_seconds))

    # ------------------------------ Epoch lifecycle ------------------------------

    def ensure_epoch(self) -> EpochBinding:
        active = self.engine.swap.active_epoch()
        if active is None:
            self.engine.compact_warm()
            self.engine.stage_epoch_from_warm()
            self.engine.cutover()
            active = self.engine.swap.active_epoch()
        if active is None:
            return EpochBinding(epoch_id="", merkle_root="")
        return EpochBinding(epoch_id=active.epoch_id, merkle_root=active.merkle_root)

    # ------------------------------ Agent indexing -------------------------------

    def index_agents(self, agents: Sequence[AgentProfile]) -> EpochBinding:
        now = time.time()
        for agent in agents:
            schemas = self._agent_schemas(agent)
            capabilities = {_norm_token(skill) for skill in agent.skills if _norm_token(skill)}
            cap_text = " ".join(sorted(capabilities))
            budget = self._agent_budget(agent)
            for schema in schemas:
                node_id = f"agent:{agent.agent_id}:{schema}"
                vector = np.array(embed_tokens(tuple(sorted(capabilities)) or (agent.agent_id,)), dtype=np.float64)
                self.engine.write(
                    VectorRecord(
                        node_id=node_id,
                        vector=vector,
                        text=f"{agent.name} {cap_text}",
                        attributes={
                            "entity": "agent",
                            "agent_id": agent.agent_id,
                            "schema": schema,
                            "budget": str(budget),
                            "budget_bucket": self._budget_bucket(budget),
                        },
                        capabilities=capabilities,
                        created_at=now,
                        weight=max(0.05, float(agent.success_rate)),
                    )
                )

        self.engine.compact_warm()
        self.engine.stage_epoch_from_warm()
        self.engine.cutover()
        return self.ensure_epoch()

    # ------------------------------ Bid state decay ------------------------------

    def ingest_bid_state(self, *, bid_id: str, task_id: str, agent: AgentProfile, state: str, confidence: float = 1.0) -> None:
        now = time.time()
        ttl = now + self.bid_state_ttl_seconds
        capabilities = {_norm_token(skill) for skill in agent.skills if _norm_token(skill)}
        vector = np.array(embed_tokens(tuple(sorted(capabilities)) or (agent.agent_id,)), dtype=np.float64)
        self.engine.write(
            VectorRecord(
                node_id=f"bid:{task_id}:{bid_id}:{agent.agent_id}",
                vector=vector,
                text=f"{state} {task_id} {agent.name}",
                attributes={
                    "entity": "bid_state",
                    "task_id": task_id,
                    "agent_id": agent.agent_id,
                    "state": _norm_token(state),
                },
                capabilities=capabilities,
                created_at=now,
                weight=max(0.01, float(confidence)),
                task_state_expires_at=ttl,
            )
        )

    # ------------------------------ Routing --------------------------------------

    def route_agents(
        self,
        task: TaskSpec,
        agents: Sequence[AgentProfile],
        *,
        required_schema: str,
        runtime_capabilities: Iterable[str],
        execution_budget: int,
        top_k: int,
        epoch_id: Optional[str] = None,
    ) -> List[RoutedAgent]:
        active = self.ensure_epoch()
        bound_epoch = epoch_id or active.epoch_id

        prequalified = {
            agent.agent_id: agent
            for agent in agents
            if self._agent_budget(agent) >= int(execution_budget)
            and _norm_token(required_schema) in self._agent_schemas(agent)
            and set(_norm_token(cap) for cap in runtime_capabilities).issubset({_norm_token(s) for s in agent.skills})
        }
        if not prequalified:
            return []

        query_tokens = tuple(_norm_token(token) for token in task.requirement_tokens()) or (task.task_id,)
        query_vector = np.array(embed_tokens(query_tokens), dtype=np.float64)
        allowed_budget_buckets = self._allowed_budget_buckets(agents, int(execution_budget))

        ranked = self.engine.query(
            QuerySpec(
                query_vector=query_vector,
                query_text=f"{task.goal} {' '.join(query_tokens)}",
                required_attributes={
                    "entity": "agent",
                    "schema": _norm_token(required_schema),
                    "budget_bucket": allowed_budget_buckets,
                },
                required_capabilities={_norm_token(cap) for cap in runtime_capabilities},
                k=max(top_k * 3, top_k),
                epoch_id=bound_epoch,
            )
        )
        if not ranked:
            return []

        per_agent: Dict[str, RankedResult] = {}
        for row in ranked:
            parts = row.node_id.split(":", 2)
            if len(parts) < 3:
                continue
            agent_id = parts[1]
            if agent_id not in prequalified:
                continue
            cur = per_agent.get(agent_id)
            if cur is None or row.score > cur.score:
                per_agent[agent_id] = row

        out = [
            RoutedAgent(
                agent=prequalified[agent_id],
                score=result.score,
                vector_score=result.vector_score,
                lexical_score=result.lexical_score,
                epoch_id=result.epoch_id,
            )
            for agent_id, result in per_agent.items()
        ]
        out.sort(key=lambda r: (-r.score, r.agent.agent_id))
        return out[:top_k]

    # ------------------------------ Epoch signing --------------------------------

    def bind_execution_payload(self, payload: Mapping[str, object], *, epoch_id: Optional[str] = None) -> Dict[str, str]:
        binding = self.ensure_epoch()
        chosen_epoch = epoch_id or binding.epoch_id
        segment = self.engine.swap.get_epoch(chosen_epoch)
        if segment is None:
            return {
                "epoch_id": "",
                "epoch_merkle_root": "",
                "epoch_binding_hash": "",
            }

        message = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(f"{segment.epoch_id}|{segment.merkle_root}|{message}".encode("utf-8")).hexdigest()
        return {
            "epoch_id": segment.epoch_id,
            "epoch_merkle_root": segment.merkle_root,
            "epoch_binding_hash": "0x" + digest,
        }

    # ------------------------------ helpers --------------------------------------

    def _agent_schemas(self, agent: AgentProfile) -> Set[str]:
        schemas = getattr(agent, "supported_schemas", ()) or ("default",)
        return {_norm_token(s) for s in schemas if _norm_token(s)}

    def _agent_budget(self, agent: AgentProfile) -> int:
        budget = getattr(agent, "execution_budget", 0) or 0
        if budget > 0:
            return int(budget)
        return int(max(1, getattr(agent, "estimated_tokens", 1)))

    def _budget_bucket(self, budget: int) -> str:
        unit = 250
        bucket = int(max(0, budget) // unit)
        return f"b{bucket}"

    def _allowed_budget_buckets(self, agents: Sequence[AgentProfile], minimum_budget: int) -> List[str]:
        max_budget = max([self._agent_budget(a) for a in agents] + [minimum_budget])
        unit = 250
        start = int(max(0, minimum_budget) // unit)
        end = int(max_budget // unit)
        return [f"b{i}" for i in range(start, end + 1)]
