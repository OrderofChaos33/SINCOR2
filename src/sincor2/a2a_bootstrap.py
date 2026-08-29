#!/usr/bin/env python3
"""
Production bootstrap for A2A + AgencyKernel.

Imported at app startup. Idempotent.
  - Binds resilient PaymentVerifier (multi-RPC, backoff, 24h cache)
  - Wraps task helpers with persistent TaskStore when possible
  - Installs AgencyKernel real-tool runtime
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("sincor.a2a.bootstrap")
_INSTALLED = False


def _install_agency_runtime() -> None:
    try:
        import sincor2.agency_kernel_runtime  # noqa: F401  — auto-installs on import
        logger.info("AgencyKernel production runtime installed")
    except Exception as err:
        logger.warning("AgencyKernel runtime install failed: %s", err)


def _install_payment_verifier() -> None:
    try:
        import sincor2.a2a_integration as a2a
        from sincor2.payment_verifier import PaymentVerifier
        import sincor2.payment_verifier as pv

        pv.AXIOM_CONTRACT = getattr(a2a, "AXIOM_CONTRACT", pv.AXIOM_CONTRACT)
        pv.TREASURY_WALLET = getattr(a2a, "TREASURY_WALLET", pv.TREASURY_WALLET)
        pv.BASE_RPC_TIMEOUT = getattr(a2a, "BASE_RPC_TIMEOUT", pv.BASE_RPC_TIMEOUT)
        pv._DEV_ENVS = getattr(a2a, "_DEV_ENVS", pv._DEV_ENVS)

        a2a.PaymentVerifier = PaymentVerifier
        logger.info("PaymentVerifier bound to resilient multi-RPC implementation")
    except Exception as err:
        logger.warning("PaymentVerifier bind failed: %s", err)


def _ensure_task_serialization() -> None:
    """Add to_dict/from_dict on A2A models if missing (older deployments)."""
    try:
        import sincor2.a2a_integration as a2a
        from sincor2.a2a_integration import A2ATask, A2AMessage, A2AArtifact, TaskState
        import uuid

        if not hasattr(A2AMessage, "from_dict"):
            @classmethod
            def _msg_from(cls, d: Dict[str, Any]) -> "A2AMessage":
                return cls(
                    message_id=d.get("messageId") or d.get("message_id") or str(uuid.uuid4()),
                    role=d.get("role", "user"),
                    parts=d.get("parts") or [],
                    context_id=d.get("contextId") or d.get("context_id"),
                    task_id=d.get("taskId") or d.get("task_id"),
                    metadata=d.get("metadata") or {},
                    extensions=d.get("extensions") or [],
                    reference_task_ids=d.get("referenceTaskIds") or d.get("reference_task_ids") or [],
                )
            A2AMessage.from_dict = _msg_from  # type: ignore[attr-defined]

        if not hasattr(A2AArtifact, "from_dict"):
            @classmethod
            def _art_from(cls, d: Dict[str, Any]) -> "A2AArtifact":
                return cls(
                    artifact_id=d.get("artifactId") or d.get("artifact_id") or str(uuid.uuid4()),
                    parts=d.get("parts") or [],
                    name=d.get("name"),
                    description=d.get("description"),
                    metadata=d.get("metadata") or {},
                    extensions=d.get("extensions") or [],
                )
            A2AArtifact.from_dict = _art_from  # type: ignore[attr-defined]

        if not hasattr(A2ATask, "to_dict"):
            def _task_to(self) -> Dict[str, Any]:
                return {
                    "id": self.id,
                    "context_id": self.context_id,
                    "skill_id": self.skill_id,
                    "input_text": self.input_text,
                    "caller_id": self.caller_id,
                    "state": self.state.value if hasattr(self.state, "value") else str(self.state),
                    "created_at": self.created_at,
                    "updated_at": self.updated_at,
                    "history": [m.to_dict() for m in self.history],
                    "artifacts": [a.to_dict() for a in self.artifacts],
                    "output": self.output,
                    "error": self.error,
                    "axm_paid": self.axm_paid,
                    "tx_hash": self.tx_hash,
                    "metadata": self.metadata,
                }
            A2ATask.to_dict = _task_to  # type: ignore[attr-defined]

        if not hasattr(A2ATask, "from_dict"):
            @classmethod
            def _task_from(cls, d: Dict[str, Any]) -> "A2ATask":
                state_raw = d.get("state", "submitted")
                if isinstance(state_raw, dict):
                    state_raw = state_raw.get("state", "submitted")
                try:
                    state = TaskState(state_raw)
                except Exception:
                    state = TaskState.SUBMITTED
                history = [
                    A2AMessage.from_dict(m) for m in (d.get("history") or [])
                    if isinstance(m, dict)
                ]
                artifacts = [
                    A2AArtifact.from_dict(a) for a in (d.get("artifacts") or [])
                    if isinstance(a, dict)
                ]
                return cls(
                    id=d["id"],
                    context_id=d.get("context_id") or d.get("contextId") or d["id"],
                    skill_id=d.get("skill_id") or "",
                    input_text=d.get("input_text") or "",
                    caller_id=d.get("caller_id") or "anonymous",
                    state=state,
                    created_at=d.get("created_at") or "",
                    updated_at=d.get("updated_at") or "",
                    history=history,
                    artifacts=artifacts,
                    output=d.get("output"),
                    error=d.get("error"),
                    axm_paid=int(d.get("axm_paid") or 0),
                    tx_hash=d.get("tx_hash"),
                    metadata=d.get("metadata") or {},
                )
            A2ATask.from_dict = _task_from  # type: ignore[attr-defined]

        logger.info("A2ATask serialization methods ensured")
    except Exception as err:
        logger.warning("A2A serialization ensure failed: %s", err)


def _install_task_store() -> None:
    try:
        import sincor2.a2a_integration as a2a
        from sincor2.a2a_task_store import get_task_store

        store = get_task_store()
        _orig_new = a2a._new_task
        _orig_get = a2a._get_task
        _orig_update = a2a._update_task

        def _new_task(*args, **kwargs):
            task = _orig_new(*args, **kwargs)
            try:
                if hasattr(task, "to_dict"):
                    store.put(task.id, task.to_dict())
            except Exception as err:
                logger.debug("task store put: %s", err)
            return task

        def _get_task(task_id: str):
            task = _orig_get(task_id)
            if task is not None:
                return task
            try:
                raw = store.get(task_id)
                if raw and hasattr(a2a.A2ATask, "from_dict"):
                    return a2a.A2ATask.from_dict(raw)
            except Exception as err:
                logger.debug("task store get: %s", err)
            return None

        def _update_task(task, **kwargs):
            task = _orig_update(task, **kwargs)
            try:
                if hasattr(task, "to_dict"):
                    store.put(task.id, task.to_dict())
            except Exception as err:
                logger.debug("task store update: %s", err)
            return task

        a2a._new_task = _new_task
        a2a._get_task = _get_task
        a2a._update_task = _update_task
        logger.info("A2A TaskStore wired  backend=%s", type(store).__name__)
    except Exception as err:
        logger.warning("TaskStore wire-up failed: %s", err)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_agency_runtime()
    _install_payment_verifier()
    _ensure_task_serialization()
    _install_task_store()
    _INSTALLED = True
    logger.info("SINCOR production bootstrap complete")


def register_a2a(app) -> bool:
    """Idempotent: install runtime + register A2ARouter discovery on *app*.

    Owns ``/.well-known/agent-card.json``, ``/.well-known/agent.json``,
    ``/api/a2a/quote``, ``/api/a2a/agents``. Safe to call twice.
    """
    try:
        install()
        names = getattr(app, "blueprints", {}) or {}
        if "a2a" in names:
            logger.info("A2ARouter already registered")
            return True
        from sincor2.a2a_integration import A2ARouter

        app.register_blueprint(A2ARouter().blueprint)
        logger.info("A2ARouter registered — discovery surfaces live")
        return True
    except Exception as err:
        logger.error("A2ARouter registration failed: %s", err)
        return False


install()
