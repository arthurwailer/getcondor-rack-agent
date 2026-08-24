# getcondor-rack-agent

Onboard agent that runs on an aircraft's communications rack and sends
media and telemetry to GetCondor (getcondor.win) in near real time.

Runs standalone on the rack — it does not depend on any third-party
software already present there. It only needs a folder to watch and,
optionally, a UDP port for live telemetry.

## What it does

- Watches `MEDIA_WATCH_DIR` for new photos, videos, and spectral GeoTIFF
  zips, and uploads each one to GetCondor as soon as it appears
  (polling-based, not inotify — see `agent/watcher.py` for why).
- Extracts capture time and GPS from standard EXIF/video metadata when
  the file doesn't carry richer metadata of its own.
- Optionally streams live telemetry (position/altitude/heading) over
  UDP to GetCondor. The telemetry source is pluggable — see
  `agent/telemetry_sources/`.
- Authenticates with `drone_id` + a per-drone token — no admin
  credentials are ever required on the rack.
- Survives restarts and Starlink-style connectivity drops: uploads
  retry with exponential backoff, and a local SQLite state file
  prevents re-uploading files already sent.

## Install

1. Create `config/.env` with:
DRONE_ID=OSCAR01
GETCONDOR_API_URL=https://getcondor.win
MQTT_TOKEN=<per-drone token>
MEDIA_WATCH_DIR=/data/watch
SCAN_INTERVAL_SECONDS=10 # optional, default 10
TELEMETRY_ENABLED=false # optional, default false
TELEMETRY_SOURCE_TYPE=raw # raw | mavlink | generic_json
TELEMETRY_UDP_HOST=0.0.0.0
TELEMETRY_UDP_PORT=14550
2. `docker compose up -d --build`

`config/.env` is gitignored — never commit real tokens.

## Structure

- `agent/watcher.py` — polls `MEDIA_WATCH_DIR`, dispatches new files by extension
- `agent/photo.py` / `agent/video.py` — extract capture time, GPS, heading from each file
- `agent/geotiff.py` — unpacks spectral zips, converts to COG, extracts fire perimeter
- `agent/uploader.py` — multipart upload to GetCondor with retry/backoff
- `agent/state.py` — SQLite dedup so restarts don't re-upload
- `agent/telemetry_orchestrator.py` — picks a telemetry source and streams samples
- `agent/telemetry_sources/` — pluggable UDP sources (`raw`, `mavlink`, `generic_json`);
  add a new source type without touching the others

## Known limitations (being addressed)

- `agent/geotiff.py` currently rejects any GPS coordinate outside a
  hardcoded Portugal mainland range — needs to become configurable
  before installing on a rack outside Portugal.
- `POST /telemetry/ingest` in `agent/uploader.py` is unconfirmed against
  the real `telemetry-svc` route — verify before relying on telemetry uploads.

## Pending

- Confirm the real `MEDIA_WATCH_DIR` path for each rack at install time
- Confirm the real telemetry ingest endpoint
- Genericize photo/video metadata extraction so a rack with a different
  file-naming convention doesn't need code changes
