web: python -m gunicorn railway_start:app --config gunicorn.conf.py
worker: celery -A sincor2.celery_app.celery worker --loglevel=info --concurrency=2 -Q sincor.long
