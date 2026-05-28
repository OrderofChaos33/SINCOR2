# SINCOR2 MVP Platform - Railway Deployment
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install only essential dependencies
# Strip any erroneous python-sendgrid line (not a real PyPI package) before installing
# Cache bust: 2026-05-28-v4  <<-- FORCED FRESH BUILD
COPY requirements.txt .
RUN sed '/^python-sendgrid/d' requirements.txt > /tmp/req_clean.txt \
    && pip install --upgrade pip \
    && pip install -r /tmp/req_clean.txt

# Copy application code (templates, static, src/sincor2, A2A all included)
COPY . .

# Verify critical new UI files made it into the image
RUN echo "=== BUILD VERIFICATION ===" && \
    ls -la /app/templates/ | head -10 && \
    ls -la /app/static/ | head -5 && \
    echo "Dashboard template exists:" && ls /app/templates/dashboard.html && \
    echo "=== END VERIFICATION ==="

# Add src to Python path so sincor2 package is importable
ENV PYTHONPATH=/app/src

# Create necessary directories
RUN mkdir -p logs outputs data

# Expose default port (Railway overrides via $PORT)
EXPOSE 8080

# Run with gunicorn for production stability
# Use shell form so $PORT env var is expanded at runtime
CMD gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 2 --timeout 120 --preload run:app