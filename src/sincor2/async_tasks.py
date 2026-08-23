"""
Celery / thread-pool job handlers for long-running SINCOR work.

Kinds:
  a2a.execute          A2A message/send dispatch
  content.generate     /admin/content/generate
  webbuilder.run       /api/webbuilder/projects/<id>/run
  webbuilder.rebuild   /api/webbuilder/projects/<id>/rebuild
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

from sincor2.task_queue import register_handler, run_job, update_job

logger = logging.getLogger("sincor.async_tasks")

try:
    from sincor2.celery_app import celery
except Exception:  # pragma: no cover — celery extra missing
    celery = None  # type: ignore[assignment]


def _celery_task(*dargs, **dkwargs):
    if celery is None:
        def _wrap(fn):
            return fn
        return _wrap
    return celery.task(*dargs, **dkwargs)


@_celery_task(bind=True, name="sincor2.execute_job", queue="sincor.long")
def execute_job(self=None, job_id: str = "") -> Any:
    if self is not None:
        try:
            update_job(job_id, celery_id=getattr(self.request, "id", None), state="running")
        except Exception:
            pass
    job = run_job(job_id)
    return job.result if job else None


def _handle_a2a(payload: Dict[str, Any], job_id: str) -> Any:
    from sincor2.a2a_integration import (
        TaskState,
        _dispatch_to_swarm,
        _finalize_a2a_task,
        _get_task,
        _update_task,
    )

    task_id = payload.get("task_id") or ""
    task = _get_task(task_id)
    if not task:
        raise RuntimeError(f"A2A task {task_id} not found (shared TaskStore required for Celery)")
    _update_task(task, state=TaskState.WORKING)
    update_job(job_id, progress=20)
    output, error = _dispatch_to_swarm(task)
    update_job(job_id, progress=85)
    finalized = _finalize_a2a_task(task, output, error)
    return finalized


def _handle_content(payload: Dict[str, Any], job_id: str) -> Any:
    from sincor2.content_agent import WordPressPublisher, generate_blog_post, init_db, save_post

    update_job(job_id, progress=10)
    init_db()
    keyword = payload.get("keyword") or ""
    ctype = payload.get("ctype") or "how-to"
    model = payload.get("model") or os.environ.get("CONTENT_MODEL", "claude-haiku-4-5")
    post = generate_blog_post(keyword, ctype, model=model)
    update_job(job_id, progress=70)
    path = save_post(post)
    result = {
        "title": post["title"],
        "slug": post["slug"],
        "word_count": post["word_count"],
        "path": str(path),
    }
    if payload.get("do_publish"):
        wp = WordPressPublisher()
        result["wordpress"] = wp.publish(post)
    return result


def _handle_webbuilder_run(payload: Dict[str, Any], job_id: str) -> Any:
    from sincor2.webbuilder_studio import run_autonomous_phases

    update_job(job_id, progress=15)
    result = run_autonomous_phases(payload.get("project_id") or "")
    return result


def _handle_webbuilder_rebuild(payload: Dict[str, Any], job_id: str) -> Any:
    from sincor2.webbuilder_studio import rebuild_draft

    update_job(job_id, progress=15)
    return rebuild_draft(payload.get("project_id") or "", prompt=payload.get("prompt"))


register_handler("a2a.execute", _handle_a2a)
register_handler("content.generate", _handle_content)
register_handler("webbuilder.run", _handle_webbuilder_run)
register_handler("webbuilder.rebuild", _handle_webbuilder_rebuild)
