FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    unzip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ ./agent/

# Carpeta donde vive el sqlite de estado (idempotencia de uploads)
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1

CMD ["python", "-u", "agent/main.py"]
