#!/bin/bash
# Runs on rack boot via systemd. Best-effort git pull, then starts the
# agent with whatever code is available -- never blocks boot waiting
# on a flaky Starlink connection (the aircraft needs to fly today even
# if the update check fails).
#
# Fetch timeout is 90s (not a shorter value like 30s): Starlink can take
# longer than that to sync satellite lock on boot, especially if the
# rack starts before the aircraft is out of a hangar or otherwise has an
# obstructed view of the sky. A too-short timeout means the rack almost
# never updates and silently drifts further behind main over time. This
# only affects the CODE update check -- media uploads and MQTT telemetry
# already retry on their own throughout the flight regardless of this
# timeout, so a slow-to-sync Starlink never blocks getting data off the
# aircraft, only how quickly new code gets picked up.
set -uo pipefail
cd "$(dirname "$0")"

echo "$(date -u +%FT%TZ) boot: checking for updates..." >> update.log

if timeout 90 git fetch origin main >> update.log 2>&1; then
    if ! git diff --quiet HEAD origin/main -- 2>/dev/null; then
        echo "$(date -u +%FT%TZ) update available, pulling..." >> update.log
        git pull origin main >> update.log 2>&1 || echo "$(date -u +%FT%TZ) pull failed, continuing with existing code" >> update.log
    else
        echo "$(date -u +%FT%TZ) already up to date" >> update.log
    fi
else
    echo "$(date -u +%FT%TZ) no network yet or fetch timed out, starting with existing code" >> update.log
fi

# Se calcula aca (en el host, donde .git existe de verdad) y se pasa
# como build-arg -- el contenedor en si nunca tiene git ni .git/, asi
# que no puede resolver su propia version por su cuenta (ver
# agent/heartbeat.py). Si por algun motivo git fallara aca, "unknown"
# nunca bloquea el arranque -- mismo principio que el resto del script.
export AGENT_VERSION="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "$(date -u +%FT%TZ) building with AGENT_VERSION=$AGENT_VERSION" >> update.log

docker compose up -d --build
