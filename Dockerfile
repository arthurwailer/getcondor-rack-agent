FROM python:3.11-slim

# Version baked in at build time (see AGENT_VERSION in
# agent/heartbeat.py for why: no git binary or .git dir exists inside
# the running container, so the version has to come from the outside,
# computed on the host where .git is real -- update-and-start.sh sets
# this via `docker compose build --build-arg AGENT_VERSION=$(git
# rev-parse --short HEAD)`, wired through docker-compose.yml's
# build.args).
ARG AGENT_VERSION=unknown
ENV AGENT_VERSION=$AGENT_VERSION

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
