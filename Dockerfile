# Multi-stage build for production (small, secure, fast, Railway-ready)
# CACHE BUST 2026-07-27-v3 — invalidate Metal builder snapshot after WeasyPrint crash
ARG CACHEBUST=20260727v3
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install to user directory (cached layer)
COPY requirements.txt .
# ReportLab only — never install WeasyPrint (needs gobject/pango system libs)
RUN pip install --user --no-cache-dir -r requirements.txt \
    && pip uninstall -y weasyprint 2>/dev/null || true

# Production runtime stage
FROM python:3.11-slim AS runtime

WORKDIR /app

# Minimal runtime deps (no WeasyPrint system libs needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libssl3 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 appuser

# Copy Python dependencies from builder
COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local

# Set environment
# NOTE: the sincor2 package lives in src/ (src layout), so /app/src must be
# on PYTHONPATH or gunicorn cannot import sincor2.mvp_app.
ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    PIP_NO_CACHE_DIR=1

# Copy application code (CACHEBUST forces this layer to rebuild)
ARG CACHEBUST=20260727v3
RUN echo "cachebust=${CACHEBUST}"
COPY --chown=appuser:appuser . .

# Create persistent data directory
RUN mkdir -p /data && chown -R appuser:appuser /data

# Switch to non-root user
USER appuser

# Healthcheck (assumes /health endpoint)
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/health' % os.environ.get('PORT', '8080'), timeout=5)" || exit 1

# Run with Gunicorn via sh so Railway's $PORT env var is expanded.
# NO --preload: master would die if any worker import fails, and heavy
# module-level schedulers delay port bind past healthcheck windows.
# Single worker for faster first /health response on Railway Metal.
CMD ["/bin/sh", "-c", \
     "gunicorn sincor2.mvp_app:app \
     --bind 0.0.0.0:${PORT} \
     --workers 1 \
     --worker-class sync \
     --timeout 180 \
     --access-logfile - \
     --error-logfile - \
     --log-level info"]
