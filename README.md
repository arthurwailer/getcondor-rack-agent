# getcondor-rack-agent

Onboard agent that runs on an aircraft's communications rack and sends
media, heartbeat, and telemetry to GetCondor (getcondor.win) in near
real time.

Runs standalone on the rack -- it does not depend on any third-party
software already present there. It only needs a folder to watch and,
optionally, a UDP port for live telemetry.

## What it does

- Watches MEDIA_WATCH_DIR (3 fixed subfolders: photos/Fotos,
  videos/Videos, spectral/Multiespectal, each nested by
  month/day/mission/OscarNN -- same real folder structure used by
  Shamrock-AddOn) for new files, and uploads each one to GetCondor as
  soon as it appears (polling-based, not inotify -- see agent/watcher.py
  for why). media_type is determined by which root subfolder a file is
  under (more reliable than file extension), and mission_id/drone_id
  are parsed from the path itself, reusing the exact same logic already
  proven in production by migrate_ftp_to_getcondor.py. A file is only
  uploaded once its mtime has been stable for at least 2x the scan
  interval, so a still-being-written file is never uploaded
  half-finished.
- On its very first run ever against a given rack, automatically
  "primes" the dedup state against whatever files already exist in
  MEDIA_WATCH_DIR (typically months of history already migrated by
  hand) -- marks them all as already-seen without uploading anything,
  then proceeds normally. No manual step required before installing.
- A file that permanently fails (corrupt zip, no known sensor inside,
  or the server rejects it with 400/403 -- e.g. plan restricted, bad
  payload) is retried exactly once, then marked permanently failed and
  silently ignored forever after, instead of being retried and
  re-logged on every scan cycle.
- Photos and videos are enriched with real capture time, GPS, and
  heading before upload -- from Shamrock-AddOn's EXIF UserComment
  (photos) or .dat sidecar (videos) when present, falling back to
  standard EXIF GPS tags otherwise. Videos are remuxed from .ts to
  .mp4 (ffmpeg -c copy, no re-encoding) before upload, since most
  browsers won't play raw MPEG-TS.
- Spectral zips are unpacked and each sensor tiff (RGBN/LWIR/KELVIN,
  identified by filename) is uploaded as an independent GEOTIFF record
  with real GPS/altitude from metadata.dat and its sensor tagged in
  metadata -- uploading each file separately (not the whole zip) means
  a Starlink drop only loses the file in progress, not the whole batch.
  The nested fire-perimeter contour (fire_contours_*.zip) is uploaded
  as-is (unprocessed) as a FIRE_PERIMETER record, so the data isn't
  lost even though COG conversion and contour processing (both would
  need GDAL) aren't implemented yet.
- Reports the running agent's version (short git commit hash, baked
  into the Docker image at build time via update-and-start.sh -- the
  running container has no git binary or .git directory, so it can't
  resolve its own version at runtime) in every heartbeat, so an admin
  can see exactly which code each rack is running from GetCondor's
  fleet panel, without SSH access.
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

Note: when started via update-and-start.sh (the normal path, see
"Auto-start and auto-update on boot" in INSTALL.md), AGENT_VERSION is
computed automatically from the current git commit and passed as a
build arg. Running `docker compose up -d --build` directly (e.g. for
manual testing) without exporting AGENT_VERSION first will build with
version "unknown" -- harmless, just won't show a real version in the
fleet panel's heartbeat.

config/.env is gitignored -- never commit real tokens.

## Structure

- agent/main.py -- entry point: starts heartbeat (background thread),
  telemetry (background thread, if enabled), and the watcher (main
  thread, auto-restarted if it ever dies unexpectedly)
- agent/watcher.py -- polls MEDIA_WATCH_DIR, determines media_type by
  root subfolder (via pathinfo.py), enriches PHOTO/VIDEO via
  photo.py/video.py before upload, handles first-run priming and
  permanent-failure tracking
- agent/pathinfo.py -- resolves media_type/mission_id/drone_id from a
  file's path within the 3-subfolder structure, mirroring
  migrate_ftp_to_getcondor.py's proven path-parsing logic
- agent/photo.py / agent/video.py -- extract capture time, GPS, heading from each file
- agent/geotiff.py -- unpacks spectral zips: uploads each sensor tiff
  (RGBN/LWIR/KELVIN) as an independent GEOTIFF, and the nested
  fire-perimeter zip as-is (unprocessed) as FIRE_PERIMETER. No COG
  conversion or contour processing yet (would need GDAL)
- agent/uploader.py -- multipart upload to GetCondor with retry/backoff
- agent/state.py -- SQLite dedup so restarts don't re-upload media,
  plus permanent-failure tracking (broken files aren't retried
  forever) and the first-run priming marker
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

- agent/geotiff.py does not convert tiffs to COG (relies entirely on
  GetCondor's server-side TiTiler processing to derive geographic
  bounds on upload) and does not process the fire-perimeter contour
  (uploads it as a raw, unprocessed zip) -- both would require adding
  GDAL to the Dockerfile, deliberately deferred rather than adding an
  untested heavy dependency the night before a real flight.
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

No need to wipe the agent-state volume for this -- dedup/priming state
is keyed by file path + size, never by drone_id or token, so changing
config/.env and restarting is enough on its own (see INSTALL.md's
"Reassigning a rack" section for the full explanation).

MEDIA_WATCH_DIR and TELEMETRY_UDP_PORT describe the physical rack
(where its cabling and disk are), not the aircraft, so they normally
stay the same across a reassignment.
