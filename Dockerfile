# syntax=docker/dockerfile:1
# SINCOR2 Railway Dockerfile — CACHE NUKE 2026-07-27-v5
# If Metal still serves WeasyPrint after this, set service env NO_CACHE=1 and redeploy.
ARG CACHEBUST=20260727v5
ARG RAILWAY_GIT_COMMIT_SHA=unknown

FROM python:3.11-slim AS builder_v5_${CACHEBUST}

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && echo "builder-cachebust=${CACHEBUST}"

COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt \
    && pip uninstall -y weasyprint pydyf 2>/dev/null || true \
    && rm -rf /root/.local/lib/python3.11/site-packages/weasyprint* \
              /root/.local/lib/python3.11/site-packages/pydyf* \
    2>/dev/null || true \
    && echo "pip-done-${CACHEBUST}"

FROM python:3.11-slim AS runtime_v5_${CACHEBUST}

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libssl3 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 appuser

COPY --from=builder_v5_${CACHEBUST} --chown=appuser:appuser /root/.local /home/appuser/.local

USER root
RUN rm -rf /home/appuser/.local/lib/python3.11/site-packages/weasyprint* \
           /home/appuser/.local/lib/python3.11/site-packages/pydyf* \
    2>/dev/null || true

ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    PIP_NO_CACHE_DIR=1

ARG CACHEBUST=20260727v5
ARG RAILWAY_GIT_COMMIT_SHA=unknown
RUN echo "copy-layer-${CACHEBUST}-commit-${RAILWAY_GIT_COMMIT_SHA}"
COPY --chown=appuser:appuser . .

# Force safe PDF import even if Metal serves a stale mvp_app.py
RUN sed -i 's/from sincor2.pdf_generator import get_pdf_generator/from sincor2.pdf_loader import get_pdf_generator/' \
        /app/src/sincor2/mvp_app.py 2>/dev/null || true \
    && printf 'cachebust=%s\ncommit=%s\nbuilt_at=2026-07-27T08:25Z\n' \
        "${CACHEBUST}" "${RAILWAY_GIT_COMMIT_SHA}" > /app/.railway-build-stamp \
    && grep -n "pdf_loader\|pdf_generator" /app/src/sincor2/mvp_app.py | head -5 || true \
    && head -20 /app/src/sincor2/pdf_generator.py || true \
    && cat /app/.railway-build-stamp

RUN mkdir -p /data && chown -R appuser:appuser /data /home/appuser/.local /app/.railway-build-stamp

USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/health' % os.environ.get('PORT', '8080'), timeout=5)" || exit 1

CMD ["/bin/sh", "-c", \
     "echo '=== SINCOR2 boot stamp ==='; cat /app/.railway-build-stamp 2>/dev/null || true; \
      rm -rf /home/appuser/.local/lib/python3.11/site-packages/weasyprint* 2>/dev/null; \
      python -c 'from sincor2.pdf_loader import get_pdf_generator; print(\"pdf_loader OK\")' || echo 'pdf_loader import soft-fail'; \
      exec gunicorn sincor2.mvp_app:app \
      --bind 0.0.0.0:${PORT} \
      --workers 1 \
      --worker-class sync \
      --timeout 180 \
      --access-logfile - \
      --error-logfile - \
      --log-level info"]
