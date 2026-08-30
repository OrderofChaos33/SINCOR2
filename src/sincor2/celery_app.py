"""
SINCOR Celery application.

Broker/backend default to REDIS_URL (or CELERY_BROKER_URL). The web process
never runs tasks inline when a broker is configured — gunicorn stays free
inside its 180s timeout.

Start a worker:

    celery -A sincor2.celery_app.celery worker --loglevel=info -Q sincor.long

See docs/ASYNC_TASK_QUEUE.md.
"""
from __future__ import annotations

import os

from celery import Celery

_broker = (
    os.getenv("CELERY_BROKER_URL")
    or os.getenv("REDIS_URL")
    or os.getenv("REDIS_PRIVATE_URL")
    or "redis://localhost:6379/0"
)
_backend = os.getenv("CELERY_RESULT_BACKEND") or _broker

celery = Celery(
    "sincor2",
    broker=_broker,
    backend=_backend,
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_time_limit=int(os.getenv("SINCOR_TASK_HARD_LIMIT", "600")),
    task_soft_time_limit=int(os.getenv("SINCOR_TASK_SOFT_LIMIT", "540")),
    task_default_queue="sincor.long",
    task_default_delivery_mode=2,
    result_expires=86400,
    broker_connection_retry_on_startup=True,
    include=["sincor2.async_tasks"],
)


def ping_broker(timeout: float = 1.5) -> bool:
    """True if the broker answers a connection attempt."""
    try:
        with celery.connection_for_write() as conn:
            conn.ensure_connection(max_retries=1, timeout=timeout)
        return True
    except Exception:
        return False
