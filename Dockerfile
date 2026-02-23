# SINCOR2 MVP Platform - Railway Deployment
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Configure Python path for package imports
ENV PYTHONPATH=/app/src

# Create runtime directories
RUN mkdir -p logs outputs data

EXPOSE 8080

# Shell form: ${PORT:-8080} expands at runtime
# Railway sets PORT env var dynamically - JSON exec form cannot expand it
CMD gunicorn --bind "0.0.0.0:${PORT:-8080}" --workers 2 --timeout 120 --log-level info --access-logfile - --error-logfile - run:app
