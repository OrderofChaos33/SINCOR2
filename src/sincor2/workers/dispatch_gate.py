"""WP3 dispatch gate: manifest-checked wrapper around task_queue.run_job.

All queue backends (eager, Celery, thread pool) funnel through
task_queue.run_job — this wrapper adds the capability-manifest check
BEFORE any handler is invoked.

Usage:
    from sincor2.workers import gated_run_job
    gated_run_job(job_id, originator="background_worker")

The originator identifies WHO/WHAT dispatched the job:
- "background_worker": scheduled/queue worker (default; cannot run
  value-moving handlers)
- "operator": human operator / head orchestrator under supervision (D5)
- "agent": autonomous agent proposal (proposal-only; value-moving rejected)

Fail-closed: unknown kinds and unauthorized value-moving dispatches raise
before the handler is touched. The job is marked FAILED with the gate
error so the denial is observable.
"""

from __future__ import annotations

import logging
from typing import Optional

from sincor2.workers.capability_manifest import check_dispatch

logger = logging.getLogger(__name__)


def gated_run_job(job_id: str, originator: str = "background_worker"):
    """Run a queued job with an explicit originator.

    Stamps the originator on the job payload, then delegates to
    task_queue.run_job, where the WP3 capability-manifest gate enforces
    the check at the single choke point (covers eager, Celery, thread
    pool — including retries, which re-enter run_job).
    """
    # Import here to avoid circulars at module load.
    from sincor2 import task_queue

    job = task_queue.get_job(job_id)
    if job is None:
        raise RuntimeError(f"job {job_id} not found")

    payload = dict(job.payload or {})
    payload["_wp3_originator"] = originator
    task_queue.update_job(job_id, payload=payload)

    return task_queue.run_job(job_id)


def gated_enqueue(
    kind: str,
    payload: Optional[dict] = None,
    *,
    originator: str = "background_worker",
    job_id: Optional[str] = None,
):
    """Enqueue with an upfront manifest check (fail fast at enqueue time).

    The gate is ALSO enforced at run time by gated_run_job, so a job
    enqueued through the raw path cannot bypass it. This check exists
    to surface misconfigurations early.
    """
    from sincor2 import task_queue

    # Fail fast: unknown kinds and unauthorized origins rejected at enqueue.
    check_dispatch(kind, originator)

    job = task_queue.enqueue(kind, payload, job_id=job_id)
    # Stamp the originator on the payload for the run-time gate.
    # (Job has no originator field; payload is the carrier.)
    payload_with_origin = dict(job.payload or {})
    payload_with_origin["_wp3_originator"] = originator
    task_queue.update_job(job.id, payload=payload_with_origin)
    return task_queue.get_job(job.id) or job


def originator_for_job(job) -> str:
    """Extract the originator stamped at enqueue time (default: background_worker)."""
    payload = job.payload or {}
    originator = payload.get("_wp3_originator", "background_worker")
    return str(originator)
