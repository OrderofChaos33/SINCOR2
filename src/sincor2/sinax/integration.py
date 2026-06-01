"""
SINAX — Phase 7 (part 1): A2A-Agnostic Integration Layer
==========================================================
Provides a canonical message format and an adapter architecture so that
SINAX can interoperate with any agent runtime without coupling to a
specific transport protocol.

Canonical objects
-----------------
    Task                — unit of work sent to SINAX
    ProofStateMessage   — portable proof-state payload
    LemmaMessage        — portable lemma payload
    ConjectureMessage   — a conjecture submitted for investigation
    VerificationResultMessage  — verifier outcome
    Trajectory          — ordered sequence of tactic steps

Adapter interface
-----------------
    BaseAdapter         — ABC for all transport adapters
    A2AAdapter          — wraps the SINCOR A2A JSON-RPC layer
    MCPAdapter          — Model Context Protocol adapter
    RESTAdapter         — direct HTTP REST adapter
    LocalAdapter        — in-process adapter (no network)

Router
------
    IntegrationRouter   — routes incoming requests to the correct SINAX
                          component based on ``task.task_type``.
    get_router()        — singleton accessor
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from . import config as cfg
from .encoder import ProofState
from .graph_store import VerificationResult

logger = logging.getLogger("sincor.sinax.integration")

# ---------------------------------------------------------------------------
# Canonical object definitions
# ---------------------------------------------------------------------------


class TaskType(str, Enum):
    ENCODE_STATE = "encode_state"
    SEARCH_TACTICS = "search_tactics"
    ANALYSE_CURVATURE = "analyse_curvature"
    DISCOVER_LEMMAS = "discover_lemmas"
    VERIFY_LEMMA = "verify_lemma"
    GET_TRAJECTORY = "get_trajectory"
    STATUS = "status"


class TaskStatus(str, Enum):
    SUBMITTED = "submitted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    """Canonical unit of work for SINAX.

    Attributes
    ----------
    task_id:
        UUID-style identifier.
    task_type:
        One of the ``TaskType`` values.
    payload:
        Free-form dict carrying task-specific parameters.
    status:
        Current lifecycle status.
    result:
        Populated once the task completes.
    created_at:
        Unix timestamp.
    caller_id:
        Identifier of the calling agent/system.
    """

    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_type: TaskType = TaskType.STATUS
    payload: Dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.SUBMITTED
    result: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)
    caller_id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["task_type"] = self.task_type.value
        d["status"] = self.status.value
        return d


@dataclass
class ProofStateMessage:
    """Portable proof-state payload."""

    goal: str
    context: str = ""
    hypotheses: List[str] = field(default_factory=list)
    tactic_history: List[str] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)

    def to_proof_state(self) -> ProofState:
        return ProofState(
            goal=self.goal,
            context=self.context,
            hypotheses=self.hypotheses,
            tactic_history=self.tactic_history,
            metadata=self.metadata,
        )

    @classmethod
    def from_dict(cls, d: dict) -> "ProofStateMessage":
        return cls(
            goal=d.get("goal", ""),
            context=d.get("context", ""),
            hypotheses=d.get("hypotheses", []),
            tactic_history=d.get("tactic_history", []),
            metadata=d.get("metadata", {}),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LemmaMessage:
    """Portable lemma payload."""

    lemma_id: str
    statement: str
    proof_sketch: str = ""
    impact_score: float = 0.0
    verification_result: str = VerificationResult.PENDING.value
    cluster_size: int = 0
    metadata: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ConjectureMessage:
    """A conjecture submitted for SINAX investigation."""

    conjecture_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    statement: str = ""
    context: str = ""
    submitter: str = ""
    metadata: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VerificationResultMessage:
    """Verifier outcome for a statement."""

    statement_id: str
    result: str  # VerificationResult.value
    proof_script: str = ""
    error_message: str = ""
    duration_seconds: float = 0.0
    metadata: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Trajectory:
    """Ordered sequence of tactic steps from start to end state."""

    trajectory_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    start_node_id: str = ""
    end_node_id: str = ""
    steps: List[Dict[str, Any]] = field(default_factory=list)
    # Each step: {"tactic": str, "node_id": str, "verified": bool, "cost": float}
    total_cost: float = 0.0
    metadata: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Adapter ABC
# ---------------------------------------------------------------------------


class BaseAdapter(ABC):
    """Abstract base class for SINAX transport adapters.

    Implementations translate between the SINAX canonical ``Task`` /
    result format and the wire format of a specific protocol.
    """

    @abstractmethod
    def submit_task(self, task: Task) -> str:
        """Submit a task; return a task_id."""

    @abstractmethod
    def get_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Poll for the result of a previously submitted task."""

    @abstractmethod
    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task; return True on success."""

    def health(self) -> Dict[str, Any]:
        """Return a health-check dict for this adapter."""
        return {"adapter": self.__class__.__name__, "status": "ok"}


# ---------------------------------------------------------------------------
# Local (in-process) adapter
# ---------------------------------------------------------------------------


class LocalAdapter(BaseAdapter):
    """In-process adapter — tasks are dispatched directly without network.

    Useful for unit testing and for the ``analytics`` rollout mode.
    """

    def __init__(self, router: Optional["IntegrationRouter"] = None) -> None:
        from .search import get_search_engine
        from .curvature import get_curvature_analyzer
        from .lemma_discovery import get_lemma_engine
        from .encoder import get_encoder
        from .graph_store import get_store

        self._router = router
        self._lock = threading.Lock()
        self._tasks: Dict[str, Task] = {}

    def submit_task(self, task: Task) -> str:
        with self._lock:
            self._tasks[task.task_id] = task
        router = self._router or get_router()
        router.dispatch(task)
        return task.task_id

    def get_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            t = self._tasks.get(task_id)
        if t is None:
            return None
        return t.result

    def cancel_task(self, task_id: str) -> bool:
        with self._lock:
            t = self._tasks.get(task_id)
            if t and t.status == TaskStatus.IN_PROGRESS:
                t.status = TaskStatus.FAILED
                return True
        return False


# ---------------------------------------------------------------------------
# A2A adapter (delegates to existing a2a_integration)
# ---------------------------------------------------------------------------


class A2AAdapter(BaseAdapter):
    """Wraps SINAX tasks as A2A JSON-RPC 2.0 messages.

    In ``analytics`` / ``suggest`` modes this is a no-op stub.  In
    ``active`` mode it posts tasks to the SINAX A2A endpoint.
    """

    def __init__(self, base_url: str = "", api_key: str = "") -> None:
        import os
        self._base_url = base_url or os.getenv("PLATFORM_URL", "http://localhost:5000")
        self._api_key = api_key or os.getenv("A2A_API_KEY", "")

    def submit_task(self, task: Task) -> str:
        logger.debug("A2AAdapter.submit_task: %s", task.task_type)
        # Wire format: wrap in A2A message/send envelope
        payload = {
            "jsonrpc": "2.0",
            "method": "message/send",
            "id": task.task_id,
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "data", "data": task.to_dict()}],
                }
            },
        }
        return self._post("/api/a2a", payload, task.task_id)

    def get_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        import urllib.request
        url = f"{self._base_url}/api/a2a/tasks/{task_id}"
        req = urllib.request.Request(url, headers={"X-API-Key": self._api_key})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            logger.warning("A2AAdapter.get_result error: %s", exc)
            return None

    def cancel_task(self, task_id: str) -> bool:
        payload = {
            "jsonrpc": "2.0",
            "method": "tasks/cancel",
            "id": str(uuid.uuid4()),
            "params": {"id": task_id},
        }
        result = self._post("/api/a2a", payload, task_id)
        return result is not None

    def _post(self, path: str, data: dict, fallback_id: str) -> Optional[str]:
        import urllib.request
        url = self._base_url + path
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-API-Key": self._api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp_data = json.loads(resp.read())
                return resp_data.get("id", fallback_id)
        except Exception as exc:
            logger.warning("A2AAdapter._post error: %s", exc)
            return None


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class MCPAdapter(BaseAdapter):
    """Model Context Protocol adapter.

    Translates SINAX tasks into MCP tool-call format.  Full implementation
    requires the ``mcp`` SDK; this class provides the structural scaffold
    and a working local-dispatch fallback.
    """

    def __init__(self) -> None:
        self._local = LocalAdapter()

    def submit_task(self, task: Task) -> str:
        # In a real deployment: serialize to MCP tool-call JSON and post to server.
        # Fallback: process locally.
        logger.debug("MCPAdapter: delegating to LocalAdapter")
        return self._local.submit_task(task)

    def get_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self._local.get_result(task_id)

    def cancel_task(self, task_id: str) -> bool:
        return self._local.cancel_task(task_id)


# ---------------------------------------------------------------------------
# REST adapter
# ---------------------------------------------------------------------------


class RESTAdapter(BaseAdapter):
    """Plain HTTP REST adapter for SINAX API endpoints."""

    def __init__(self, base_url: str = "", api_key: str = "") -> None:
        import os
        self._base_url = base_url or os.getenv("PLATFORM_URL", "http://localhost:5000")
        self._api_key = api_key or os.getenv("SINAX_API_KEY", "")

    def submit_task(self, task: Task) -> str:
        return self._post("/api/sinax/tasks", task.to_dict()) or task.task_id

    def get_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self._get(f"/api/sinax/tasks/{task_id}")

    def cancel_task(self, task_id: str) -> bool:
        result = self._post(f"/api/sinax/tasks/{task_id}/cancel", {})
        return result is not None

    def _post(self, path: str, data: dict) -> Optional[Any]:
        import urllib.request
        url = self._base_url + path
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-SINAX-Key": self._api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            logger.warning("RESTAdapter._post error: %s", exc)
            return None

    def _get(self, path: str) -> Optional[dict]:
        import urllib.request
        url = self._base_url + path
        req = urllib.request.Request(url, headers={"X-SINAX-Key": self._api_key})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            logger.warning("RESTAdapter._get error: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Integration Router
# ---------------------------------------------------------------------------


class IntegrationRouter:
    """Routes canonical ``Task`` objects to the appropriate SINAX component.

    All dispatch is synchronous in the current implementation.  For async
    or high-throughput environments, replace ``dispatch`` with a background
    thread-pool or Celery task.
    """

    def dispatch(self, task: Task) -> None:
        """Dispatch ``task`` to the correct handler.  Mutates ``task.result``."""
        task.status = TaskStatus.IN_PROGRESS
        try:
            handler = self._HANDLERS.get(task.task_type)
            if handler is None:
                task.result = {"error": f"Unknown task_type: {task.task_type}"}
                task.status = TaskStatus.FAILED
                return
            task.result = handler(self, task)
            task.status = TaskStatus.COMPLETED
        except Exception as exc:
            logger.exception("IntegrationRouter.dispatch error: %s", exc)
            task.result = {"error": str(exc)}
            task.status = TaskStatus.FAILED

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_status(self, task: Task) -> dict:
        from . import config as cfg
        return {"sinax_enabled": cfg.SINAX_ENABLED, "mode": cfg.SINAX_MODE}

    def _handle_encode_state(self, task: Task) -> dict:
        from .encoder import get_encoder

        ps_dict = task.payload.get("proof_state", {})
        ps = ProofStateMessage.from_dict(ps_dict).to_proof_state()
        encoder = get_encoder()
        emb = encoder.encode(ps)
        return {
            "proof_state_hash": emb.proof_state_hash,
            "model_version": emb.model_version,
            "dim": emb.dim,
            "vector_preview": emb.vector[:8].tolist(),
        }

    def _handle_search_tactics(self, task: Task) -> dict:
        from .search import get_search_engine

        ps_dict = task.payload.get("proof_state", {})
        goal_dict = task.payload.get("goal_state")
        ps = ProofStateMessage.from_dict(ps_dict).to_proof_state()
        goal_ps = ProofStateMessage.from_dict(goal_dict).to_proof_state() if goal_dict else None
        engine = get_search_engine()
        candidates = engine.search(ps, goal_ps)
        if candidates is None:
            return {"tactics": [], "fallback": True}
        return {
            "tactics": [
                {"tactic": c.tactic, "score": c.score, "provenance": c.provenance}
                for c in candidates
            ],
            "fallback": False,
        }

    def _handle_analyse_curvature(self, task: Task) -> dict:
        from .curvature import get_curvature_analyzer

        node_id = task.payload.get("node_id", "")
        radius = int(task.payload.get("radius", 2))
        analyzer = get_curvature_analyzer()
        rc = analyzer.compute(node_id, radius=radius)
        if rc is None:
            return {"curvature": None, "reason": "insufficient_observations"}
        return {"curvature": rc.to_dict()}

    def _handle_discover_lemmas(self, task: Task) -> dict:
        from .lemma_discovery import get_lemma_engine

        engine = get_lemma_engine()
        candidates = engine.discover()
        return {"lemmas": [c.to_dict() for c in candidates]}

    def _handle_verify_lemma(self, task: Task) -> dict:
        from .lemma_discovery import Verifier

        statement = task.payload.get("statement", "")
        proof_sketch = task.payload.get("proof_sketch", "")
        verifier = Verifier()
        result = verifier.verify(statement, proof_sketch)
        return {"verification_result": result.value}

    def _handle_get_trajectory(self, task: Task) -> dict:
        from .graph_store import get_store

        start = task.payload.get("start_node_id", "")
        end = task.payload.get("end_node_id", "")
        store = get_store()
        path = store.reconstruct_path(start, end)
        if path is None:
            return {"trajectory": None}
        steps = []
        for node, edge in path:
            steps.append({
                "node_id": node.node_id,
                "goal": node.proof_state.goal,
                "tactic": edge.tactic if edge else None,
                "verified": edge.verified if edge else None,
                "cost": edge.cost if edge else None,
            })
        traj = Trajectory(
            start_node_id=start,
            end_node_id=end,
            steps=steps,
            total_cost=sum(s["cost"] or 0.0 for s in steps),
        )
        return {"trajectory": traj.to_dict()}

    _HANDLERS = {
        TaskType.STATUS: _handle_status,
        TaskType.ENCODE_STATE: _handle_encode_state,
        TaskType.SEARCH_TACTICS: _handle_search_tactics,
        TaskType.ANALYSE_CURVATURE: _handle_analyse_curvature,
        TaskType.DISCOVER_LEMMAS: _handle_discover_lemmas,
        TaskType.VERIFY_LEMMA: _handle_verify_lemma,
        TaskType.GET_TRAJECTORY: _handle_get_trajectory,
    }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_router_instance: Optional[IntegrationRouter] = None
_router_lock = threading.Lock()


def get_router() -> IntegrationRouter:
    """Return the process-global ``IntegrationRouter`` singleton."""
    global _router_instance
    if _router_instance is None:
        with _router_lock:
            if _router_instance is None:
                _router_instance = IntegrationRouter()
    return _router_instance
