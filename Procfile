web: python -m gunicorn sincor2.mvp_app:app --bind 0.0.0.0:${PORT:-8080} --workers 1 --worker-class sync --timeout 180 --access-logfile - --error-logfile - --log-level info
