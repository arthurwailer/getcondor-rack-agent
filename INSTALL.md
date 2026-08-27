# Installing getcondor-rack-agent on a new rack

This guide walks through installing the agent on a physical rack for
the first time. It assumes the rack runs Ubuntu and has network access
to getcondor.win.

## 1. Requirements on the rack

- Docker + Docker Compose installed (docker --version, docker compose version)
- Network access to https://getcondor.win (port 443)
- If telemetry is used: UDP access to the source (autopilot/gimbal) on
  whatever local port it broadcasts on
- A folder where photos/videos/GeoTIFF zips already land (ask whoever
  operates the camera/gimbal system for the exact path)

## 2. Get the code onto the rack

git clone git@github.com:arthurwailer/getcondor-rack-agent.git
cd getcondor-rack-agent

(or git pull if it's already cloned and you're updating)

## 3. Get a drone token

Each aircraft/drone needs its own drone_id + token, generated from
GetCondor's admin panel (or via the API's regenerate-token endpoint).
Do this from your Mac, not the rack -- the token is a secret.

Write down:
- drone_id (e.g. OSCAR01)
- token (looks like a long hex string)

## 4. Configure the rack

On the rack:

mkdir -p config
nano config/.env

Paste, filling in the real values:

DRONE_ID=OSCAR01
GETCONDOR_API_URL=https://getcondor.win
MQTT_TOKEN=REPLACE_WITH_REAL_TOKEN
MEDIA_WATCH_DIR=REPLACE_WITH_REAL_PATH
SCAN_INTERVAL_SECONDS=10
TELEMETRY_ENABLED=false

Save and exit (Ctrl+O, Enter, Ctrl+X in nano).

Do not commit this file. It's already in .gitignore.

## 5. Verify the watch folder exists and has the expected files

ls -la REPLACE_WITH_REAL_PATH

If it doesn't exist yet, either create it or fix the path in config/.env
before continuing -- the agent will just log a warning and do nothing
if the folder is missing.

## 6. Start the agent

docker compose up -d --build

## 7. Confirm it's running and picking up files

docker logs -f getcondor-rack-agent

You should see:

getcondor-rack-agent starting for drone_id=OSCAR01
watcher starting -- folder: /data/watch, interval: 10s

Drop a test photo into the watch folder and confirm it logs "upload OK"
within one scan interval.

## 8. Confirm it appears in GetCondor

Log into getcondor.win, open Mission Browser, and toggle "Show tests"
if the file was a test upload without a real mission_id. If it doesn't
appear, check the logs for "upload failed" or "403" errors first.

## 9. (Optional) Enable telemetry

Only after step 7 is confirmed working. Add to config/.env:

TELEMETRY_ENABLED=true
TELEMETRY_SOURCE_TYPE=raw
TELEMETRY_UDP_HOST=0.0.0.0
TELEMETRY_UDP_PORT=REPLACE_WITH_REAL_PORT

Restart: docker compose up -d --build

With TELEMETRY_SOURCE_TYPE=raw, the agent won't upload anything yet --
it just logs each incoming UDP packet so you can see the real format
and confirm the port is reachable. Once you know the format, switch to
mavlink or generic_json (see README.md for field mapping).

## Troubleshooting

Symptom: "MEDIA_WATCH_DIR does not exist yet" in logs
Likely cause: wrong path in config/.env, or folder not mounted into the
container -- check docker-compose.yml volumes.

Symptom: files never upload, no error logged
Likely cause: filename doesn't match the expected timestamp pattern --
check agent/photo.py and agent/geotiff.py logs. GPS validity is NOT
checked by the rack agent -- it uploads whatever coordinates it reads
(or none) and lets GetCondor's backend decide whether the position is
plausible (see drone-platform's isPlausibleCoordinate). This is
intentional: keeping the geographic bounds check in one place (the
backend) avoids the rack and backend silently drifting out of sync if
the allowed region ever changes.

Symptom: upload fails with 403
Likely cause: wrong or expired MQTT_TOKEN, or plan restriction on that
drone_id -- confirm the token in GetCondor's admin panel.

Symptom: telemetry never uploads, no packets logged even in raw mode
Likely cause: wrong TELEMETRY_UDP_PORT, or a firewall on the rack
blocking that UDP port -- confirm with tcpdump on the rack.

## Reassigning a rack to a different aircraft

Racks get physically swapped between aircraft when there's a hardware
issue. The aircraft identity (OSCAR01/02/03) never changes -- the rack
just takes on whatever identity the aircraft it's currently installed
in has. To move an already-configured rack to a different aircraft:

nano config/.env
(change DRONE_ID to the aircraft this rack is now installed in)
docker compose up -d --build

To also wipe the local upload-dedup history (so the rack doesn't
"remember" files from the aircraft it used to serve):

docker compose down
docker volume rm getcondor-rack-agent_agent-state
docker compose up -d --build

## Auto-start and auto-update on boot

The aircraft powers the rack on and off daily, so the agent needs to
start itself (and check for updates) every time the rack boots --
nobody is going to SSH in each morning.

1. Copy the systemd service file into place:

sudo cp getcondor-rack-agent.service /etc/systemd/system/

2. Edit the paths inside it if the repo isn't cloned at
/opt/getcondor-rack-agent (adjust WorkingDirectory and ExecStart/ExecStop
to match the real path).

3. Enable and start it:

sudo systemctl daemon-reload
sudo systemctl enable getcondor-rack-agent.service
sudo systemctl start getcondor-rack-agent.service

4. Verify it's active:

sudo systemctl status getcondor-rack-agent.service

From now on, every time the rack boots, update-and-start.sh runs
automatically: it does a best-effort git pull (skipped silently if
there's no network yet or it takes longer than 30s), then starts the
agent with whatever code is on disk. A missed update never blocks the
aircraft from flying that day -- it just runs the previous version
until the next successful boot-time check.

Update log: tail -f update.log in the repo root.
