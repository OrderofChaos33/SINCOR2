"""
SINCOR async job queue.

Long-running Flask work (A2A dispatch, content generation, webbuilder)
must not run on the gunicorn sync worker — timeout is 180s and a blocked
worker 504s the whole site.

Backends (auto-selected, override with SINCOR_TASK_QUEUE):
  eager   FLASK_ENV=test — run inline so existing pytest still settles
  celery  REDIS_URL/CELERY_BROKER_URL reachable and celery importable
  thread  in-process ThreadPoolExecutor (unsticks gunicorn without Redis)

HTTP contract: enqueue() returns immediately with a Job. Callers respond
202 Accepted + task_id; clients poll GET /api/tasks/<id>.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("sincor.queue")

QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELED = "canceled"

_TERMINAL = frozenset({COMPLETED, FAILED, CANCELED})

Handler = Callable[[Dict[str, Any], str], Any]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_queue() -> str:
    return (os.getenv("SINCOR_TASK_QUEUE") or "").strip().lower()


def is_test_env() -> bool:
    env = (os.getenv("FLASK_ENV") or os.getenv("ENVIRONMENT") or "").lower()
    return env in ("test", "testing")


def is_eager() -> bool:
    forced = _env_queue()
    if forced == "eager":
        return True
    if forced in ("celery", "thread"):
        return False
    return is_test_env()


def _celery_configured() -> bool:
    return bool(
        (os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL") or os.getenv("REDIS_PRIVATE_URL") or "").strip()
    )


def detect_backend() -> str:
    forced = _env_queue()
    if forced in ("eager", "celery", "thread"):
        if forced == "celery" and not _celery_configured():
            logger.warning("SINCOR_TASK_QUEUE=celery but no broker URL; falling back to thread")
            return "thread"
        return forced
    if is_eager():
        return "eager"
    if _celery_configured():
        try:
            from sincor2.celery_app import ping_broker

            if ping_broker():
                return "celery"
            logger.warning("Celery broker configured but unreachable; using thread pool")
        except Exception as exc:
            logger.warning("Celery unavailable (%s); using thread pool", exc)
    return "thread"


@dataclass
class Job:
    id: str
    kind: str
    state: str
    payload: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: Optional[str] = None
    progress: int = 0
    created_at: str = ""
    updated_at: str = ""
    celery_id: Optional[str] = None

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.id,
            "kind": self.kind,
            "status": self.state,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "poll_url": f"/api/tasks/{self.id}",
        }

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Job":
        known = {k: data.get(k) for k in (
            "id", "kind", "state", "payload", "result", "error",
            "progress", "created_at", "updated_at", "celery_id",
        )}
        known.setdefault("payload", {})
        known.setdefault("progress", 0)
        return cls(**known)  # type: ignore[arg-type]


class _MemoryJobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._order: List[str] = []
        self._lock = threading.Lock()

    def put(self, job: Job) -> None:
        with self._lock:
            if job.id not in self._jobs:
                self._order.append(job.id)
            self._jobs[job.id] = job.to_dict()

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            raw = self._jobs.get(job_id)
        return Job.from_dict(raw) if raw else None

    def list_recent(self, limit: int = 50) -> List[Job]:
        with self._lock:
            ids = list(reversed(self._order[-limit:]))
            rows = [self._jobs[i] for i in ids if i in self._jobs]
        return [Job.from_dict(r) for r in rows]


class _RedisJobStore:
    def __init__(self, url: str) -> None:
        import redis

        self._r = redis.Redis.from_url(url, decode_responses=True)
        self._ttl = int(os.getenv("SINCOR_JOB_TTL_SECONDS", "86400"))

    def put(self, job: Job) -> None:
        pipe = self._r.pipeline()
        pipe.set(f"sincor:job:{job.id}", json.dumps(job.to_dict()), ex=self._ttl)
        pipe.lpush("sincor:jobs", job.id)
        pipe.ltrim("sincor:jobs", 0, 499)
        pipe.execute()

    def get(self, job_id: str) -> Optional[Job]:
        raw = self._r.get(f"sincor:job:{job_id}")
        if not raw:
            return None
        return Job.from_dict(json.loads(raw))

    def list_recent(self, limit: int = 50) -> List[Job]:
        ids = self._r.lrange("sincor:jobs", 0, limit - 1)
        jobs: List[Job] = []
        for jid in ids:
            job = self.get(jid)
            if job:
                jobs.append(job)
        return jobs


_store_lock = threading.Lock()
_store: Any = None
_handlers: Dict[str, Handler] = {}
_pool: Optional[ThreadPoolExecutor] = None
_backend_cached: Optional[str] = None


def _job_store():
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is not None:
            return _store
        url = (os.getenv("REDIS_URL") or os.getenv("REDIS_PRIVATE_URL") or "").strip()
        if url:
            try:
                _store = _RedisJobStore(url)
                _store.get("__probe__")
                logger.info("Job store: redis")
                return _store
            except Exception as exc:
                logger.warning("Redis job store unavailable (%s); using memory", exc)
        _store = _MemoryJobStore()
        logger.info("Job store: memory")
        return _store


def _thread_pool() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        workers = int(os.getenv("SINCOR_QUEUE_WORKERS", "4"))
        _pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sincor-q")
    return _pool


def register_handler(kind: str, fn: Handler) -> None:
    _handlers[kind] = fn


def get_handler(kind: str) -> Optional[Handler]:
    if kind in _handlers:
        return _handlers[kind]
    try:
        import sincor2.async_tasks  # noqa: F401 — registers handlers on import
    except Exception as exc:
        logger.debug("async_tasks import skipped: %s", exc)
    return _handlers.get(kind)


def current_backend() -> str:
    global _backend_cached
    if _backend_cached is None:
        _backend_cached = detect_backend()
    return _backend_cached


def reset_backend_cache() -> None:
    global _backend_cached
    _backend_cached = None


def put_job(job: Job) -> None:
    job.updated_at = _now()
    _job_store().put(job)


def get_job(job_id: str) -> Optional[Job]:
    return _job_store().get(job_id)


def list_jobs(limit: int = 50) -> List[Job]:
    return _job_store().list_recent(limit)


def update_job(job_id: str, **fields: Any) -> Optional[Job]:
    job = get_job(job_id)
    if not job:
        return None
    for key, value in fields.items():
        setattr(job, key, value)
    put_job(job)
    return job


def run_job(job_id: str) -> Job:
    """Execute a queued job. Called by Celery workers and the thread pool."""
    job = get_job(job_id)
    if not job:
        raise RuntimeError(f"job {job_id} not found")
    if job.state in _TERMINAL:
        return job
    update_job(job_id, state=RUNNING, progress=5)
    handler = get_handler(job.kind)
    if handler is None:
        update_job(job_id, state=FAILED, error=f"no handler for kind '{job.kind}'")
        raise RuntimeError(f"no handler for kind '{job.kind}'")
    try:
        result = handler(job.payload or {}, job_id)
        updated = update_job(job_id, state=COMPLETED, result=result, progress=100, error=None)
        return updated or job
    except Exception as exc:
        logger.exception("Job %s (%s) failed", job_id, job.kind)
        update_job(job_id, state=FAILED, error=str(exc), progress=100)
        raise


def enqueue(kind: str, payload: Optional[Dict[str, Any]] = None, *, job_id: Optional[str] = None) -> Job:
    """
    Accept a job and return immediately (except eager/test).

    Never blocks the gunicorn worker on the real work.
    """
    job = Job(
        id=job_id or str(uuid.uuid4()),
        kind=kind,
        state=QUEUED,
        payload=payload or {},
        created_at=_now(),
        updated_at=_now(),
    )
    put_job(job)
    backend = current_backend()
    logger.info("Enqueue %s kind=%s backend=%s", job.id, kind, backend)

    if backend == "eager":
        run_job(job.id)
        return get_job(job.id) or job

    if backend == "celery":
        try:
            from sincor2.async_tasks import execute_job

            async_result = execute_job.delay(job.id)
            update_job(job.id, celery_id=async_result.id)
            return get_job(job.id) or job
        except Exception as exc:
            logger.warning("Celery delay failed (%s); falling back to thread", exc)

    _thread_pool().submit(_safe_run, job.id)
    return job


def _safe_run(job_id: str) -> None:
    try:
        run_job(job_id)
    except Exception:
        pass


def accepted_payload(job: Job) -> Dict[str, Any]:
    body = job.to_public_dict()
    body["accepted"] = True
    return body


def queue_health() -> Dict[str, Any]:
    backend = current_backend()
    detail = backend
    redis_url = bool((os.getenv("REDIS_URL") or os.getenv("REDIS_PRIVATE_URL") or "").strip())
    return {
        "backend": backend,
        "eager": backend == "eager",
        "redis": redis_url,
        "handlers": sorted(_handlers.keys()),
        "detail": detail,
    }


def register_flask_routes(app: Any) -> None:
    """Attach GET /api/tasks and GET /api/tasks/<id> onto a Flask app."""

    @app.route("/api/tasks", methods=["GET"])
    def _list_queue_jobs():
        from flask import jsonify, request

        try:
            limit = min(int(request.args.get("limit") or 50), 200)
        except (TypeError, ValueError):
            limit = 50
        return jsonify({
            "backend": current_backend(),
            "tasks": [j.to_public_dict() for j in list_jobs(limit)],
        })

    @app.route("/api/tasks/<job_id>", methods=["GET"])
    def _get_queue_job(job_id: str):
        from flask import jsonify

        job = get_job(job_id)
        if not job:
            return jsonify({"error": "not_found", "task_id": job_id}), 404
        return jsonify(job.to_public_dict())
