FROM python:3.12-slim

WORKDIR /app

# Dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

# Non-root user
RUN useradd -r -s /bin/false qbitx
USER qbitx

EXPOSE 8080

# Gunicorn: 4 workers, bind to all interfaces
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "4", "--timeout", "35", "server:app"]
