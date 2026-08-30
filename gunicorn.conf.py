"""Gunicorn configuration — reads PORT from the environment.

Using a Python config file means no shell-variable expansion is needed in
the start command, so it works whether Railway executes the command via a
shell or in exec/array form.

sync workers + timeout=180: long jobs (A2A, content, webbuilder) MUST go
through sincor2.task_queue (Celery/Redis or the thread-pool fallback) and
return 202. Do not raise this timeout to "fix" 504s — that just blocks the
only worker for longer. Streamed A2A (message/stream) stays on the socket
on purpose; everything else is async.
"""
import os

bind = "0.0.0.0:{}".format(os.environ.get("PORT", "8080"))
workers = 1
worker_class = "sync"
timeout = 180
accesslog = "-"
errorlog = "-"
loglevel = "info"
