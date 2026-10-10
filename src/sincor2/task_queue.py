"""
SINCOR async job queue.

Long-running Flask work (A2A dispatch, content generation, webbuilder)
must not run on the gunicorn sync worker — timeout is 180s and a blocked
worker 504s the whole site.

Backends (auto-selected, override with SINCOR_TASK_QUEUE):
  eager   FLASK_ENV=test — run inline so existing pytest still settles
  celery  REDIS_URL/CELERY_BROKER_URL reachable and celery importable
  thread  in-process ThreadPoolExecutor (unsticks gunicorn without Redis)

Durability contract (C2, P2 audit):
  * When the celery backend is active, job state lives in the SHARED Redis
    job store — never in process-local memory — so a Celery worker in
    another process sees the same job the web process enqueued.
  * With no broker configured the app runs in an explicit, loudly-logged
    DEGRADED mode (in-process thread pool + process-local job state).
  * ``verify_queue_readiness()`` is the boot-time gate: an explicitly
    configured but unreachable backend fails boot fast instead of booting
    a half-working queue.
  * ``enqueue()`` never falls back to silent inline execution. A broker
    failure raises :class:`QueueEnqueueError` (retryable); the caller
    decides whether to retry (HTTP 503 + Retry-After at the API layer).

HTTP contract: enqueue() returns immediately with a Job. Callers respond
202 Accepted + task_id; clients poll GET /api/tasks/<id>.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from flask_jwt_extended import get_jwt_identity, jwt_required, verify_jwt_in_request

logger = logging.getLogger("sincor.queue")

QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELED = "canceled"

_TERMINAL = frozenset({COMPLETED, FAILED, CANCELED})

Handler = Callable[[Dict[str, Any], str], Any]


class QueueConfigError(RuntimeError):
    """The queue is misconfigured (explicit backend requested but unusable).

    Raised by the boot-time readiness gate and by backend detection for
    static misconfiguration. Never swallowed: the app must not boot into a
    half-working queue.
    """


class QueueEnqueueError(RuntimeError):
    """The job was NOT accepted by the queue (broker/store failure).

    Nothing ran — it is safe for the caller to retry. API layers map this
    to HTTP 503 with a Retry-After hint; the system never silently executes
    the job inline instead.
    """

    def __init__(self, message: str, retry_after: int = 30):
        super().__init__(message)
        self.retry_after = retry_after
        self.retryable = True


# Default Retry-After (seconds) advertised to callers when the queue is down.
QUEUE_RETRY_AFTER_SECONDS = int(os.getenv("SINCOR_QUEUE_RETRY_AFTER", "30"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Matches absolute filesystem-path tokens with 2+ segments ("/app/secrets/x").
# Used to scrub internal paths out of API-facing error text. URLs are
# preserved: the lookbehind excludes matches that follow "://" or a word char.
_PATH_TOKEN_RE = re.compile(r"(?<![\w:/])(?:/[\w.\-~]+){2,}")


def _public_error_text(err: Any) -> str:
    """Redact a job error for API responses.

    First line only (never a traceback), truncated, with filesystem-path
    tokens replaced — internal paths, provider payloads and stack frames
    must not reach API consumers.
    """
    lines = str(err or "").strip().splitlines()
    first = lines[0] if lines else ""
    first = _PATH_TOKEN_RE.sub("[path]", first)
    return first[:280]


def _request_tenant() -> str:
    """Best-effort tenant (JWT identity) for the current request, else "".

    Resolution order: verified Bearer JWT -> signed ``access_token`` cookie
    (form-login sessions) -> "". ``decode_token`` verifies the signature,
    so the cookie path trusts nothing unverified. Never raises.
    """
    try:
        from flask import has_request_context, request

        if not has_request_context():
            return ""
        try:
            verify_jwt_in_request(optional=True)
            ident = get_jwt_identity()
            if ident:
                return str(ident).strip()
        except Exception:
            pass
        try:
            from flask_jwt_extended import decode_token

            token = (request.cookies.get("access_token") or "").strip()
            if token:
                sub = decode_token(token).get("sub")
                if sub:
                    return str(sub).strip()
        except Exception:
            pass
    except Exception:
        pass
    return ""


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
    """Select the queue backend. Never silently degrades an explicit choice.

    Raises :class:`QueueConfigError` when ``SINCOR_TASK_QUEUE=celery`` is
    set but no broker URL is configured at all — that is a static operator
    error, not something to paper over with a quiet fallback.
    """
    forced = _env_queue()
    if forced in ("eager", "celery", "thread"):
        if forced == "celery" and not _celery_configured():
            raise QueueConfigError(
                "SINCOR_TASK_QUEUE=celery but no broker URL is configured "
                "(set CELERY_BROKER_URL or REDIS_URL)"
            )
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


def _shared_redis_url() -> str:
    """Redis URL usable as the shared job store (redis:// or rediss:// only)."""
    for key in ("REDIS_URL", "REDIS_PRIVATE_URL", "CELERY_BROKER_URL",
                "CELERY_RESULT_BACKEND"):
        value = (os.getenv(key) or "").strip()
        if value and value.split("://", 1)[0] in ("redis", "rediss"):
            return value
    return ""


def describe_backend() -> Dict[str, Any]:
    """Explicit, inspectable account of the effective queue mode.

    ``degraded`` is True whenever the app is running without a broker in a
    non-test environment: the thread pool still works, but job state is
    process-local and invisible to other workers — operators must see that.
    """
    forced = _env_queue()
    try:
        backend = detect_backend()
    except QueueConfigError as exc:
        return {
            "backend": "misconfigured",
            "degraded": True,
            "degraded_reason": str(exc),
            "job_store": "none",
            "explicit": bool(forced),
        }
    degraded = False
    reason = ""
    if backend == "thread" and not is_eager() and not is_test_env():
        if forced == "thread":
            reason = ("operator-selected in-process thread pool; no broker — "
                      "job state is process-local to this worker")
        elif _celery_configured():
            degraded = True
            reason = ("broker configured but unreachable — DEGRADED: in-process "
                      "thread pool with process-local job state")
        else:
            degraded = True
            reason = ("no broker configured — DEGRADED: in-process thread pool "
                      "with process-local job state; set CELERY_BROKER_URL/REDIS_URL "
                      "for shared durable execution")
    store = "redis" if _shared_redis_url() else "memory"
    return {
        "backend": backend,
        "degraded": degraded,
        "degraded_reason": reason,
        "job_store": store,
        "explicit": bool(forced),
    }


def verify_queue_readiness() -> Dict[str, Any]:
    """Boot-time readiness gate for the async queue.

    Verifies that the *configured* backend is actually reachable and that
    job storage is shared when it must be. Raises :class:`QueueConfigError`
    with a clear message instead of booting a half-working queue.

    Call once at application startup (mvp_app does this). Safe in test/eager
    mode — that path is intentionally inline and always "ready".
    """
    forced = _env_queue()
    report = describe_backend()
    backend = report["backend"]
    if backend == "misconfigured":
        raise QueueConfigError(report["degraded_reason"])

    if backend == "eager":
        logger.info("Queue readiness: eager (test) backend — inline execution")
        return {**report, "ready": True}

    if backend == "celery":
        try:
            from sincor2.celery_app import ping_broker
        except Exception as exc:
            raise QueueConfigError(
                "Queue backend 'celery' was selected but the Celery "
                f"application could not be loaded ({exc})"
            ) from exc

        if not ping_broker():
            raise QueueConfigError(
                "Queue backend 'celery' was selected"
                + (" (SINCOR_TASK_QUEUE=celery)" if forced == "celery" else "")
                + " but the broker is unreachable. Refusing to boot a "
                  "half-working queue: fix CELERY_BROKER_URL/REDIS_URL or "
                  "unset SINCOR_TASK_QUEUE."
            )
        # The shared job store is mandatory on the celery path: a Celery
        # worker in another process must see the jobs the web process wrote.
        store = _job_store()  # raises QueueConfigError unless Redis-backed
        if not isinstance(store, _RedisJobStore):
            raise QueueConfigError(
                "Queue backend 'celery' requires the shared Redis job store, "
                "but job storage resolved to process-local memory. Set REDIS_URL "
                "(or a redis:// CELERY_BROKER_URL)."
            )
        logger.info("Queue readiness: celery backend OK; shared Redis job store OK")
        return {**report, "ready": True}

    # thread backend: allowed, but never silent about being degraded.
    if report["degraded"]:
        logger.warning(
            "Queue readiness: DEGRADED — %s", report["degraded_reason"]
        )
    else:
        logger.info("Queue readiness: thread backend (%s)", report["degraded_reason"])
    return {**report, "ready": True}


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
    # Owning tenant (JWT identity of the submitter). Jobs stamped with ""
    # are internal/legacy and are never served over the API (fail closed).
    # Deliberately excluded from to_public_dict().
    tenant_id: str = ""

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.id,
            "kind": self.kind,
            "status": self.state,
            "progress": self.progress,
            "result": self.result,
            "error": _public_error_text(self.error),
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
            "tenant_id",
        )}
        known.setdefault("payload", {})
        known.setdefault("progress", 0)
        if not known.get("tenant_id"):
            # Legacy records predate tenant stamping: stay fail-closed ("").
            known["tenant_id"] = ""
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
    """Return the job store for the effective backend.

    Celery path: the store MUST be the shared Redis store. A Celery worker
    runs in a different process, so process-local memory would make enqueued
    jobs invisible to it ("job not found"). Raises :class:`QueueConfigError`
    instead of silently falling back to memory.
    """
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is not None:
            return _store
        backend = current_backend()
        url = _shared_redis_url()
        if backend == "celery":
            if not url:
                raise QueueConfigError(
                    "celery backend requires shared Redis job storage; set "
                    "REDIS_URL (or a redis:// CELERY_BROKER_URL)"
                )
            try:
                _store = _RedisJobStore(url)
                _store.get("__probe__")  # fail fast if Redis is unreachable
            except Exception as exc:
                raise QueueConfigError(
                    f"celery backend requires shared Redis job storage; "
                    f"Redis unreachable ({exc})"
                ) from exc
            logger.info("Job store: redis (shared, celery backend)")
            return _store
        if url:
            try:
                _store = _RedisJobStore(url)
                _store.get("__probe__")
                logger.info("Job store: redis")
                return _store
            except Exception as exc:
                logger.warning("Redis job store unavailable (%s); using memory", exc)
        _store = _MemoryJobStore()
        logger.warning(
            "Job store: process-local memory — job state is NOT visible to "
            "other processes/workers"
        )
        return _store


def reset_job_store() -> None:
    """Test hook: drop the cached job store (and thread pool)."""
    global _store, _pool
    with _store_lock:
        _store = None
    _pool = None


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


def enqueue(
    kind: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    job_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> Job:
    """
    Accept a job and return immediately (except eager/test).

    Never blocks the gunicorn worker on the real work.

    ``tenant_id`` stamps job ownership. When omitted, it is derived from
    the current request's JWT identity (or ""). Pass it explicitly from
    background producers that act on a user's behalf.
    Raises :class:`QueueEnqueueError` (retryable) when the job cannot be
    handed to the configured backend. The job record is left in the store
    marked failed-to-queue so it never lingers as misleadingly "queued",
    but NOTHING is executed inline — the caller decides whether to retry.
    """
    job = Job(
        id=job_id or str(uuid.uuid4()),
        kind=kind,
        state=QUEUED,
        payload=payload or {},
        created_at=_now(),
        updated_at=_now(),
        tenant_id=tenant_id if tenant_id is not None else _request_tenant(),
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
            # C2 (P2 audit): NO silent fallback to thread/inline execution.
            # A broker failure changes failure semantics if papered over;
            # the caller gets a retriable error and decides.
            update_job(job.id, state=FAILED,
                       error=f"enqueue_failed: broker rejected job ({exc}); safe to retry")
            raise QueueEnqueueError(
                f"queue unavailable: broker rejected job {job.id} ({exc})",
                retry_after=QUEUE_RETRY_AFTER_SECONDS,
            ) from exc

    try:
        _thread_pool().submit(_safe_run, job.id)
    except Exception as exc:
        update_job(job.id, state=FAILED,
                   error=f"enqueue_failed: thread pool rejected job ({exc}); safe to retry")
        raise QueueEnqueueError(
            f"queue unavailable: thread pool rejected job {job.id} ({exc})",
            retry_after=QUEUE_RETRY_AFTER_SECONDS,
        ) from exc
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
    try:
        report = describe_backend()
    except Exception as exc:  # never 500 the health endpoint on config errors
        return {
            "backend": "misconfigured",
            "degraded": True,
            "degraded_reason": str(exc),
            "job_store": "none",
            "handlers": sorted(_handlers.keys()),
        }
    backend = report["backend"]
    return {
        "backend": backend,
        "eager": backend == "eager",
        "redis": bool(_shared_redis_url()),
        "degraded": report["degraded"],
        "degraded_reason": report["degraded_reason"],
        "job_store": report["job_store"],
        "handlers": sorted(_handlers.keys()),
        "detail": report["degraded_reason"] or backend,
    }


def register_flask_routes(app: Any) -> None:
    """Attach GET /api/tasks and GET /api/tasks/<id> onto a Flask app.

    Both endpoints require a Bearer JWT and enforce tenant ownership: a
    caller only ever sees jobs stamped with their own JWT identity.
    Internal/legacy jobs with an empty tenant are never served (fail
    closed). Error text is redacted to a single scrubbed line — no
    tracebacks, internal paths, or provider payloads.
    """

    @app.route("/api/tasks", methods=["GET"])
    @jwt_required()
    def _list_queue_jobs():
        from flask import jsonify, request

        tenant = str(get_jwt_identity() or "")
        try:
            limit = min(int(request.args.get("limit") or 50), 200)
        except (TypeError, ValueError):
            limit = 50
        mine = [
            j.to_public_dict()
            for j in list_jobs(limit)
            if j.tenant_id and j.tenant_id == tenant
        ]
        return jsonify({
            "backend": current_backend(),
            "tasks": mine,
        })

    @app.route("/api/tasks/<job_id>", methods=["GET"])
    @jwt_required()
    def _get_queue_job(job_id: str):
        from flask import jsonify

        tenant = str(get_jwt_identity() or "")
        job = get_job(job_id)
        if not job:
            return jsonify({"error": "not_found", "task_id": job_id}), 404
        if not job.tenant_id or job.tenant_id != tenant:
            return jsonify({"error": "forbidden"}), 403
        return jsonify(job.to_public_dict())
