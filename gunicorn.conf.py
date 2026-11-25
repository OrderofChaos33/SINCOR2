import os

bind = "0.0.0.0:{}".format(os.environ.get("PORT", "8080"))
# Concurrency (perf fix, 2026-11-24): exactly ONE worker PROCESS, but
# threaded. The A2A marketplace state (sincor2.a2a_inbound.Fabric:
# agents / tasks / bids / commits) is a per-process in-memory
# singleton loaded from the volume at boot and guarded by a
# threading.Lock — additional worker PROCESSES would each load their
# own copy of the board and diverge (writes in one process invisible
# to the others), so `workers` must stay 1 while state lives in
# process memory. The old sync worker served one request at a time:
# a single slow request (GET /v1/a2a/tasks took 3-9 s in production)
# queued EVERY other route behind it — /health and /pricing waited
# 12-47 s (Railway network logs, 2026-11-24). Threads share the one
# process (and the one Fabric + lock), so cheap requests are served
# while a slow board read runs. 16 threads is deliberately modest:
# the box runs ~1.1 vCPU / ~0.7 GB of its 24 vCPU / 24 GB, the work is
# GIL-bound Python, and the heaviest poller (the liveness runner)
# fetches board pages with a small bounded pool — 16 covers that
# burst plus interactive traffic without pretending CPU scales.
workers = 1
worker_class = "gthread"
threads = 16
timeout = 180
preload_app = False
accesslog = "-"
# WREN PR 7: explicit access-log format WITHOUT the query string.
# Intake and download URLs carry bearer tokens in ?token=..., and the
# gunicorn default format logs %(r)s — the full request line, query
# included — into Railway's plain-text logs. %(U)s is the URL path only
# (no query), so tokens never reach the access log.
access_log_format = '%(h)s %(t)s "%(m)s %(U)s" %(s)s %(b)s %(L)s'
errorlog = "-"
loglevel = "info"


def post_worker_init(worker):
    """Bind already happened. Warm mvp_app off the request thread."""
    import railway_start

    railway_start.start_background_load()
