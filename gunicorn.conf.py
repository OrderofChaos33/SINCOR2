import os

bind = "0.0.0.0:{}".format(os.environ.get("PORT", "8080"))
workers = 1
worker_class = "sync"
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
