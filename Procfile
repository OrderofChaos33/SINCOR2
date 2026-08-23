web: python -m gunicorn sincor2.mvp_app:app --config gunicorn.conf.py
worker: celery -A sincor2.celery_app.celery worker --loglevel=info --concurrency=2 -Q sincor.long