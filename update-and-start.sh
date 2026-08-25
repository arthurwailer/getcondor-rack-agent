#!/bin/bash
# Runs on rack boot via systemd. Best-effort git pull, then starts the
# agent with whatever code is available -- never blocks boot waiting
# on a flaky Starlink connection (the aircraft needs to fly today even
# if the update check fails).
set -uo pipefail
cd "$(dirname "$0")"

echo "$(date -u +%FT%TZ) boot: checking for updates..." >> update.log

if timeout 30 git fetch origin main >> update.log 2>&1; then
    if ! git diff --quiet HEAD origin/main -- 2>/dev/null; then
        echo "$(date -u +%FT%TZ) update available, pulling..." >> update.log
        git pull origin main >> update.log 2>&1 || echo "$(date -u +%FT%TZ) pull failed, continuing with existing code" >> update.log
    else
        echo "$(date -u +%FT%TZ) already up to date" >> update.log
    fi
else
    echo "$(date -u +%FT%TZ) no network yet or fetch timed out, starting with existing code" >> update.log
fi

docker compose up -d --build
