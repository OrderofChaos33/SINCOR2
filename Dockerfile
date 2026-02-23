# SINCOR2 MVP Platform - Railway Deployment
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install only essential dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Force cache invalidation for COPY step
ENV BUILD_VERSION=2026-02-23-v3

# Copy application code (cache busted by BUILD_VERSION above)
COPY . .

# Add src to Python path so sincor2 package is importable
ENV PYTHONPATH=/app/src

# Create necessary directories
RUN mkdir -p logs outputs data

# Expose default port (Railway overrides via $PORT)
EXPOSE 8080

# Default CMD - railway.json startCommand overrides this
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "120", "--log-level", "info", "--access-logfile", "-", "run:app"]
