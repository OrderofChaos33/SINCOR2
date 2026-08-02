"""Gunicorn configuration — reads PORT from the environment.

Using a Python config file means no shell-variable expansion is needed in
the start command, so it works whether Railway executes the command via a
shell or in exec/array form.
"""
import os

bind = "0.0.0.0:{}".format(os.environ.get("PORT", "8080"))
workers = 1
worker_class = "sync"
timeout = 180
accesslog = "-"
errorlog = "-"
loglevel = "info"
