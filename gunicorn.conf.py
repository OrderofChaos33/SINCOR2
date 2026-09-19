import os

bind = "0.0.0.0:{}".format(os.environ.get("PORT", "8080"))
workers = 1
worker_class = "sync"
timeout = 180
preload_app = False
accesslog = "-"
errorlog = "-"
loglevel = "info"
