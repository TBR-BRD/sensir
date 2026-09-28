FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Erkennt ein stilles Einfrieren des Prozesses (siehe app/health.py) - reiner
# "antwortet der HTTP-Server" wie bei curl/wget würde das NICHT erkennen,
# live beobachtet 2026-09-28. /healthz meldet 503, wenn der Heartbeat-
# Scheduler-Job zu lange nicht mehr lief.
HEALTHCHECK --interval=60s --timeout=10s --start-period=45s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=5)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
