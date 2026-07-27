# Multi-stage build for SINCOR2 production
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python requirements
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# Production runtime stage
FROM python:3.11-slim AS runtime

WORKDIR /app

# Install runtime dependencies, including system libs for WeasyPrint
# WeasyPrint is imported in src/sincor2/pdf_generator.py for PDF generation
# and requires these system libraries to function at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libssl3 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libglib2.0-0 \
    libffi8 \
    libharfbuzz0b \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd -m -u 1000 appuser

# Copy Python packages from builder stage
COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local

# Set environment variables
ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

# Copy application source
COPY --chown=appuser:appuser . .

# Create persistent data directory
RUN mkdir -p /data && chown -R appuser:appuser /data

# Switch to non-root user
USER appuser

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/health' % os.environ.get('PORT', '8080'), timeout=5)" || exit 1

# Start gunicorn via shell to allow $PORT expansion
CMD ["/bin/sh", "-c", \
     "gunicorn sincor2.mvp_app:app \
     --bind 0.0.0.0:${PORT} \
     --workers 2 \
     --worker-class sync \
     --timeout 180 \
     --access-logfile - \
     --error-logfile - \
     --log-level info"]

