# SINCOR2 MVP Platform - Railway Deployment
# Build v4 - 2026-02-23
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies - change comment to bust cache
# v4 cache bust
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Add src to Python path so sincor2 package is importable
ENV PYTHONPATH=/app/src

# Create necessary directories
RUN mkdir -p logs outputs data

# Expose default port (Railway overrides via $PORT)
EXPOSE 8080

# Default fallback CMD
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "120", "--log-level", "info", "--access-logfile", "-", "--error-logfile", "-", "run:app"]
