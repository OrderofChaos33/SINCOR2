FROM python:3.12

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p logs outputs data

EXPOSE 8080

# Shell form so $PORT expands at runtime
CMD gunicorn --bind "0.0.0.0:${PORT:-8080}" --workers 2 --timeout 120 --log-level info --access-logfile - --error-logfile - run:app
