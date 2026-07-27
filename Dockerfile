# Multi-stage build for production (Railway-ready)
# CACHE BUST 2026-07-27-v4 — Metal snapshot was serving pre-WeasyPrint-removal code
ARG CACHEBUST=20260727v4
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Force pip layer rebuild when CACHEBUST changes
RUN echo "pip-cachebust=${CACHEBUST}" \
    && pip install --user --no-cache-dir -r requirements.txt \
    && pip uninstall -y weasyprint pydyf 2>/dev/null || true \
    && rm -rf /root/.local/lib/python3.11/site-packages/weasyprint* 2>/dev/null || true

FROM python:3.11-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libssl3 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 appuser

COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local

# Purge WeasyPrint again in runtime layer (Metal cache can reintroduce it)
USER root
RUN rm -rf /home/appuser/.local/lib/python3.11/site-packages/weasyprint* \
           /home/appuser/.local/lib/python3.11/site-packages/pydyf* \
    2>/dev/null || true \
    && echo "weasyprint-purged-${CACHEBUST:-v4}"

ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    PIP_NO_CACHE_DIR=1

ARG CACHEBUST=20260727v4
RUN echo "codecopy-cachebust=${CACHEBUST}"
COPY --chown=appuser:appuser . .

# Rewrite hard pdf import → safe loader (works even if Metal serves stale mvp_app)
RUN sed -i 's/from sincor2.pdf_generator import get_pdf_generator/from sincor2.pdf_loader import get_pdf_generator/' \
        /app/src/sincor2/mvp_app.py \
    && grep -n "pdf_loader\|pdf_generator" /app/src/sincor2/mvp_app.py | head -5

RUN mkdir -p /data && chown -R appuser:appuser /data /home/appuser/.local

USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/health' % os.environ.get('PORT', '8080'), timeout=5)" || exit 1

# Runtime WeasyPrint purge + single worker, no preload
CMD ["/bin/sh", "-c", \
     "rm -rf /home/appuser/.local/lib/python3.11/site-packages/weasyprint* 2>/dev/null; \
      python -c 'import sincor2.pdf_loader; print(\"pdf_loader ok\")' || true; \
      gunicorn sincor2.mvp_app:app \
      --bind 0.0.0.0:${PORT} \
      --workers 1 \
      --worker-class sync \
      --timeout 180 \
      --access-logfile - \
      --error-logfile - \
      --log-level info"]
