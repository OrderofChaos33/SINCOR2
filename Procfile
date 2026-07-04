web: python -m gunicorn sincor2.mvp_app:app --bind 0.0.0.0:$PORT --workers 2 --worker-class sync --timeout 180 --preload-app --access-logfile - --error-logfile - --log-level info
