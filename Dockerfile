# SINCOR Master Platform - Railway Deployment
FROM python:3.14.0-alpine3.22 AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install only essential dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p logs outputs data

# Expose Railway port
EXPOSE 8080

# Run with production WSGI server (Gunicorn)
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8080", "--workers", "4", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]