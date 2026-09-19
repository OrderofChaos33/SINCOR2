import os

bind = "0.0.0.0:{}".format(os.environ.get("PORT", "8080"))
workers = 1
worker_class = "sync"
timeout = 180
preload_app = False
accesslog = "-"
errorlog = "-"
loglevel = "info"


def post_worker_init(worker):
    """Bind already happened. Warm mvp_app off the request thread."""
    import railway_start

    railway_start.start_background_load()
