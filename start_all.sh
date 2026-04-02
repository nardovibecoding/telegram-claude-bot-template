#!/bin/bash
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
# Start all persona bots with auto-restart on crash/stop.
# Usage:
#   ./start_all.sh          — start all bots
#   ./start_all.sh stop     — stop all bots cleanly
cd "$(dirname "$0")"
source venv/bin/activate

# Anti-zombie: prevent multiple start_all.sh instances
LOCKFILE="$(pwd)/.start_all.lock"
if [ "$1" != "stop" ]; then
    if [ -f "$LOCKFILE" ] && kill -0 "$(cat "$LOCKFILE" 2>/dev/null)" 2>/dev/null; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] start_all.sh already running (PID $(cat $LOCKFILE)), exiting."
        exit 0
    fi
    echo $$ > "$LOCKFILE"
fi

PIDFILE="$(pwd)/.bot_pids"

stop_all() {
    if [ -f "$PIDFILE" ]; then
        while read -r pid; do
            kill -- -"$pid" 2>/dev/null   # kill process group
        done < "$PIDFILE"
        rm -f "$PIDFILE"
        echo "All bots stopped."
    else
        echo "No PID file found. Killing by name..."
        pkill -9 -f "run_bot.py" 2>/dev/null
        pkill -9 -f "admin_bot.py" 2>/dev/null
    fi
}

if [ "$1" = "stop" ]; then
    stop_all
    exit 0
fi

# Rotate log if over 5000 lines — truncate in-place to preserve open fd
LOG="/tmp/start_all.log"
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 5000 ]; then
    cp "$LOG" "$LOG.old"  # full backup (overwritten each rotation)
    tail -2000 "$LOG" > "$LOG.tmp" && cat "$LOG.tmp" > "$LOG" && rm "$LOG.tmp"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Log rotated (old saved to $LOG.old)" >> "$LOG"
fi

# Stop any existing instances first
stop_all 2>/dev/null

run_with_restart() {
    local name="$1"
    shift
    while true; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting $name..."
        "$@"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $name stopped (exit code $?), restarting in 5s..."
        sleep 5
    done
}

# Start each in its own process group (set -m gives each bg job its own pgid)
set -m

# Auto-discover and launch all persona bots from personas/*.json
for config in personas/*.json; do
    [ -f "$config" ] || continue
    # Skip the example template
    basename_f=$(basename "$config" .json)
    [ "$basename_f" = "example" ] && continue
    pid=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['id'])" "$config" 2>/dev/null)
    if [ -n "$pid" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Auto-discovered persona: $pid"
        run_with_restart "$pid" python run_bot.py "$pid" &
    fi
done

# Admin bot (always runs — not a persona)
run_with_restart "Admin"     python admin_bot.py &

# Save process group IDs for clean shutdown
jobs -p > "$PIDFILE"

# Watchdog: kill frozen admin bot if heartbeat goes stale AND not busy
HEARTBEAT="$(pwd)/.admin_heartbeat"
BUSY_FILE="$(pwd)/.admin_busy"
HEARTBEAT_STALE=180   # consider stale after 3 min of no heartbeat
BUSY_MAX=900          # kill even if "busy" after 15 min (frozen mid-job)
admin_watchdog() {
    sleep 120  # give admin bot time to start and write first heartbeat
    while true; do
        sleep 60
        if [ -f "$HEARTBEAT" ]; then
            last=$(cat "$HEARTBEAT" 2>/dev/null)
            now=$(date +%s)
            age=$(( now - last ))
            if [ "$age" -gt "$HEARTBEAT_STALE" ]; then
                # Heartbeat is stale — check if a job is actively running
                if [ -f "$BUSY_FILE" ]; then
                    busy_ts=$(cat "$BUSY_FILE" 2>/dev/null)
                    busy_age=$(( now - busy_ts ))
                    if [ "$busy_age" -lt "$BUSY_MAX" ]; then
                        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Watchdog: heartbeat stale (${age}s) but job active (${busy_age}s) — waiting"
                        continue
                    fi
                    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Watchdog: frozen mid-job (busy ${busy_age}s), killing..."
                else
                    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Watchdog: heartbeat stale (${age}s), no active job — killing..."
                fi
                pkill -9 -f "admin_bot.py" 2>/dev/null
                rm -f "$HEARTBEAT" "$BUSY_FILE" "$(pwd)/.locks/admin_bot.pid"
            fi
        fi
    done
}
admin_watchdog &

trap 'stop_all; rm -f "$LOCKFILE"; exit 0' INT TERM
wait
