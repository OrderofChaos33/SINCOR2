# SINCOR2 Railway Dockerfile

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
    ca-certificates \
    libssl3 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 appuser

COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local

ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONPATH=/app:/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    PIP_NO_CACHE_DIR=1

COPY --chown=appuser:appuser . .
RUN mkdir -p /data && chown -R appuser:appuser /data /home/appuser/.local

USER appuser

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; p=os.environ.get('PORT','8080'); urllib.request.urlopen('http://127.0.0.1:%s/health'%p, timeout=3)"

CMD ["python", "-m", "gunicorn", "railway_start:app", "--config", "gunicorn.conf.py"]
