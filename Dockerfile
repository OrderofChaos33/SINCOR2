# SINCOR2 MVP Platform - Railway Deployment
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install only essential dependencies
# Strip any erroneous python-sendgrid line (not a real PyPI package) before installing
# Cache bust: v3
COPY requirements.txt .
RUN sed '/^python-sendgrid/d' requirements.txt > /tmp/req_clean.txt \
    && pip install --upgrade pip \
    && pip install -r /tmp/req_clean.txt

# Copy application code
COPY . .

# Add src to Python path so sincor2 package is importable
ENV PYTHONPATH=/app/src

# Create necessary directories (mount Railway volume at /data/webbuilder for persistence)
RUN mkdir -p logs outputs data /data/webbuilder
ENV WEBBUILDER_DATA_DIR=/data/webbuilder

# Expose default port (Railway overrides via $PORT)
EXPOSE 8080

# Run with gunicorn for production stability
# Use shell form so $PORT env var is expanded at runtime
CMD gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 2 --timeout 120 --preload run:app