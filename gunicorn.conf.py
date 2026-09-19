"""Gunicorn configuration — reads PORT from the environment.

Entry point MUST be sincor2.wsgi:app so /health is callable before mvp_app
finishes importing. Do not point gunicorn at sincor2.mvp_app:app in Railway.
"""
import os

bind = "0.0.0.0:{}".format(os.environ.get("PORT", "8080"))
workers = 1
worker_class = "sync"
timeout = 180
preload_app = False
accesslog = "-"
errorlog = "-"
loglevel = "info"
