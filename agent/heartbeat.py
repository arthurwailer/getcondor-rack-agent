"""
Periodically pushes a small status ping to GetCondor -- uptime, disk
space, last upload time, uploads in the last hour. Lets an admin see
the health of every rack in the fleet from GetCondor's dashboard,
without connecting to any individual rack over SSH.

This is intentionally separate from telemetry: telemetry is about
where the aircraft is; heartbeat is about whether the agent itself
is alive and working.
"""

import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone

import requests

import state

logger = logging.getLogger("heartbeat")

GETCONDOR_API_URL = os.getenv("GETCONDOR_API_URL", "https://getcondor.win").rstrip("/")
DRONE_ID = os.getenv("DRONE_ID", "UNKNOWN")
MQTT_TOKEN = os.getenv("MQTT_TOKEN", "")
HEARTBEAT_INTERVAL_SECONDS = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "300"))

HEARTBEAT_URL_PATH = "/drones/heartbeat"  # TODO: confirm against real backend route

_started_at = time.time()


def _resolve_agent_version() -> str:
    """Version of the code actually running -- resolved once at import
    time, not on every heartbeat, since it never changes for the life of
    the process (update-and-start.sh only pulls new code on the next
    boot, not while the agent is already running).

    Preferred source: the AGENT_VERSION env var, baked into the Docker
    image at build time (see Dockerfile's ARG/ENV AGENT_VERSION and
    docker-compose.yml's build.args -- update-and-start.sh computes it
    via `git rev-parse --short HEAD` on the host, where .git actually
    exists, right before `docker compose up -d --build`). The running
    container has no .git directory and no git binary, so calling git
    from inside the container would always fail -- this was tried and
    confirmed broken before switching to the build-arg approach.

    Fallback: git rev-parse directly, for the case where the agent runs
    outside Docker (e.g. local development/testing) and .git is right
    there next to the code. Falls back to "unknown" if neither works."""
    env_version = os.getenv("AGENT_VERSION", "").strip()
    if env_version and env_version != "unknown":
        return env_version

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        logger.warning("could not resolve agent version from git", exc_info=True)
    return "unknown"


AGENT_VERSION = _resolve_agent_version()


def _disk_free_gb(path: str) -> float:
    try:
        usage = shutil.disk_usage(path)
        return round(usage.free / (1024 ** 3), 1)
    except OSError:
        return -1.0


def _build_payload() -> dict:
    last_upload = state.get_last_upload_time()
    by_type = state.get_last_upload_time_by_type()
    return {
        "drone_id": DRONE_ID,
        "token": MQTT_TOKEN,
        "agent_version": AGENT_VERSION,
        "reported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "uptime_seconds": int(time.time() - _started_at),
        "disk_free_gb": _disk_free_gb(os.getenv("MEDIA_WATCH_DIR", "/data/watch")),
        "last_upload_at": last_upload,
        "uploads_last_hour": state.count_uploaded_last_hour(),
        "last_photo_at": by_type.get("PHOTO"),
        "last_video_at": by_type.get("VIDEO"),
        "last_geotiff_at": by_type.get("GEOTIFF_ZIP"),
    }


def run() -> None:
    logger.info("heartbeat starting -- drone_id=%s, interval=%ds", DRONE_ID, HEARTBEAT_INTERVAL_SECONDS)
    while True:
        try:
            payload = _build_payload()
            resp = requests.post(
                f"{GETCONDOR_API_URL}{HEARTBEAT_URL_PATH}", json=payload, timeout=10
            )
            resp.raise_for_status()
            logger.info("heartbeat sent OK")
        except requests.RequestException as exc:
            logger.warning("heartbeat failed (will retry next interval): %s", exc)
        except Exception:
            logger.exception("unexpected error building/sending heartbeat")

        time.sleep(HEARTBEAT_INTERVAL_SECONDS)
