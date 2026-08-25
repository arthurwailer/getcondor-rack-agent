# getcondor-rack-agent

Onboard agent that runs on an aircraft's communications rack and sends
media, heartbeat, and telemetry to GetCondor (getcondor.win) in near
real time.

Runs standalone on the rack -- it does not depend on any third-party
software already present there. It only needs a folder to watch and,
optionally, a UDP port for live telemetry.

## What it does

- Watches MEDIA_WATCH_DIR for new photos, videos, and spectral GeoTIFF
  zips, and uploads each one to GetCondor as soon as it appears
  (polling-based, not inotify -- see agent/watcher.py for why).
- Extracts capture time and GPS from standard EXIF/video metadata when
  the file doesn't carry richer metadata of its own.
- Reports a periodic heartbeat (uptime, disk free, last upload per
  media type) so the fleet status is visible in the GetCondor web
  panel without SSH-ing into any rack.
- Streams live telemetry (position/altitude/heading/battery) from a
  pluggable UDP source (raw / mavlink / generic_json -- see
  agent/telemetry_sources/) to GetCondor's real telemetry-svc over
  MQTT, using a durable local queue: a sample is written to disk
  before any network attempt, and is only removed once the broker
  confirms delivery (QoS 1 PUBACK). Nothing is lost on a Starlink drop
  -- it queues locally and drains automatically once the link returns.
- Authenticates with drone_id + a per-drone token -- no admin
  credentials are ever required on the rack.
- Survives restarts: uploads retry with exponential backoff, and a
  local SQLite state file prevents re-uploading files already sent.

## Install

1. Create config/.env with:

DRONE_ID=OSCAR01
GETCONDOR_API_URL=https://getcondor.win
MQTT_BROKER_HOST=getcondor.win
MQTT_BROKER_PORT=1883
MQTT_TOKEN=<per-drone token>
MEDIA_WATCH_DIR=/data/watch
SCAN_INTERVAL_SECONDS=10
HEARTBEAT_INTERVAL_SECONDS=300
TELEMETRY_ENABLED=false
TELEMETRY_SOURCE_TYPE=raw
TELEMETRY_UDP_HOST=0.0.0.0
TELEMETRY_UDP_PORT=14550

2. docker compose up -d --build

config/.env is gitignored -- never commit real tokens.

## Structure

- agent/watcher.py -- polls MEDIA_WATCH_DIR, dispatches new files by extension
- agent/photo.py / agent/video.py -- extract capture time, GPS, heading from each file
- agent/geotiff.py -- unpacks spectral zips, converts to COG, extracts fire perimeter
- agent/uploader.py -- multipart upload to GetCondor with retry/backoff
- agent/state.py -- SQLite dedup so restarts don't re-upload media
- agent/heartbeat.py -- periodic fleet-status ping to GetCondor
- agent/telemetry_orchestrator.py -- store-and-forward: reads samples
  from the configured source, writer thread persists them durably,
  separate sender thread drains the queue to MQTT with confirmed delivery
- agent/telemetry_queue.py -- durable SQLite outbox for telemetry samples
- agent/telemetry_sources/ -- pluggable UDP sources (raw, mavlink,
  generic_json); add a new source type without touching the others
- agent/telemetry_pb2.py -- compiled protobuf schema, copied from
  drone-platform/tools/rpi-agent/telemetry_pb2.py (must match the
  schema telemetry-svc expects)

## Known limitations (being addressed)

- agent/geotiff.py currently rejects any GPS coordinate outside a
  hardcoded Portugal mainland range -- needs to become configurable
  before installing on a rack outside Portugal.
- photo.py/video.py currently parse capture time via a filename
  regex tuned to Shamrock's naming convention (metaOSD.py). A rack
  with a different naming convention will silently fail to upload
  until this is genericized.
- Timeout value in uploader.py's multipart upload has been
  inconsistent across revisions (60s vs 180s seen) -- confirm the
  current value is appropriate for large GeoTIFF uploads over Starlink
  before relying on it.

## Pending

- Confirm the real MEDIA_WATCH_DIR path and TELEMETRY_UDP_PORT for
  each physical rack at install time -- these describe the hardware,
  not the aircraft, so they don't change when a rack is reassigned to
  a different aircraft (see below).
- First real-hardware test of agent/telemetry_sources/mavlink_udp.py
  against an actual gimbal/autopilot -- only tested so far against
  simulated MAVLink-shaped samples and the real MQTT/telemetry-svc
  backend.
- Genericize photo/video metadata extraction so a rack with a
  different file-naming convention doesn't need code changes.

## Reassigning a rack to a different aircraft

Racks get physically swapped between aircraft when there's a hardware
issue. The aircraft identity (OSCAR01/02/03) never changes -- the rack
just takes on whatever identity the aircraft it's currently installed
in has. To move an already-configured rack to a different aircraft:

nano config/.env
(change DRONE_ID to the aircraft this rack is now installed in, and
MQTT_TOKEN to that aircraft's real token)
docker compose up -d --build

To also wipe the local upload-dedup history (so the rack doesn't
"remember" files from the aircraft it used to serve):

docker compose down
docker volume rm getcondor-rack-agent_agent-state
docker compose up -d --build

MEDIA_WATCH_DIR and TELEMETRY_UDP_PORT describe the physical rack
(where its cabling and disk are), not the aircraft, so they normally
stay the same across a reassignment.
