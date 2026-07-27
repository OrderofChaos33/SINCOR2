# SINCOR2 Railway Dockerfile
# 2026-07-27-v6: valid stage names (ARG cannot be used in AS aliases)
ARG CACHEBUST=20260727v6

FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
ARG CACHEBUST=20260727v6
RUN echo "pip-cachebust=${CACHEBUST}" \
    && pip install --user --no-cache-dir -r requirements.txt \
    && pip uninstall -y weasyprint pydyf 2>/dev/null || true \
    && rm -rf /root/.local/lib/python3.11/site-packages/weasyprint* \
              /root/.local/lib/python3.11/site-packages/pydyf* \
       2>/dev/null || true

FROM python:3.11-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libssl3 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 appuser

COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local

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

ARG CACHEBUST=20260727v6
RUN echo "copy-cachebust=${CACHEBUST}"
COPY --chown=appuser:appuser . .

RUN sed -i 's/from sincor2.pdf_generator import get_pdf_generator/from sincor2.pdf_loader import get_pdf_generator/' \
        /app/src/sincor2/mvp_app.py 2>/dev/null || true \
    && printf 'cachebust=%s\n' "${CACHEBUST}" > /app/.railway-build-stamp

RUN mkdir -p /data && chown -R appuser:appuser /data /home/appuser/.local /app/.railway-build-stamp

USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/health' % os.environ.get('PORT', '8080'), timeout=5)" || exit 1

CMD ["/bin/sh", "-c", \
     "cat /app/.railway-build-stamp 2>/dev/null || true; \
      rm -rf /home/appuser/.local/lib/python3.11/site-packages/weasyprint* 2>/dev/null; \
      exec gunicorn sincor2.mvp_app:app \
      --bind 0.0.0.0:${PORT} \
      --workers 1 \
      --worker-class sync \
      --timeout 180 \
      --access-logfile - \
      --error-logfile - \
      --log-level info"]
