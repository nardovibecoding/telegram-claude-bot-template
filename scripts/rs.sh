#!/bin/bash
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
# Quick restart for services. Usage: rs [service]
# rs          = restart bots
# rs bots     = restart persona bots (kill, start_all.sh auto-restarts)
# rs reminder = restart reminder daemon
# rs all      = restart everything

SERVICE="${1:-bots}"

case "$SERVICE" in
    bots|persona)
        echo "Killing persona bots (start_all.sh will auto-restart)..."
        pkill -f 'run_bot.py' || true
        sleep 5
        pgrep -af 'run_bot.py' | grep -v pgrep
        ;;
    reminder)
        echo "Restarting reminder daemon..."
        systemctl --user restart reminder
        sleep 2
        systemctl --user status reminder --no-pager | head -5
        ;;
    all)
        echo "Restarting everything..."
        pkill -f 'run_bot.py' || true
        sleep 5
        echo "--- Status ---"
        pgrep -af 'run_bot.py' | grep -v pgrep | wc -l | xargs -I{} echo "persona bots: {} running"
        ;;
    *)
        echo "Usage: rs [bots|reminder|all]"
        ;;
esac
