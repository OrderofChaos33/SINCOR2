# SINCOR2 Clean Railway Dockerfile
# Simplified - no fragile hacks, no code mutation at build, consistent gunicorn start
# 2026-07-27 clean v1 - fixes 9 weeks of deployment pain

FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

FROM python:3.11-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libssl3 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 appuser

COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local

ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY --chown=appuser:appuser . .

RUN mkdir -p /data && chown -R appuser:appuser /data /home/appuser/.local

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "
import os, urllib.request
port = os.environ.get('PORT', '8080')
try:
    urllib.request.urlopen(f'http://localhost:{port}/health', timeout=5)
except Exception:
    exit(1)
" || exit 1

CMD ["gunicorn", "sincor2.mvp_app:app", "--bind", "0.0.0.0:${PORT:-8080}", "--workers", "1", "--worker-class", "sync", "--timeout", "180", "--access-logfile", "-", "--error-logfile", "-", "--log-level", "info"]
