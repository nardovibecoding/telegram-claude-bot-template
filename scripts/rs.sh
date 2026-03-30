#!/bin/bash
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
# Quick restart for services. Usage: rs [service]
# rs          = restart auto-reply
# rs autoreply = restart auto-reply
# rs bots     = restart persona bots (kill, start_all.sh auto-restarts)
# rs reminder = restart reminder daemon
# rs all      = restart everything

SERVICE="${1:-autoreply}"

case "$SERVICE" in
    autoreply|ar|reply)
        echo "Restarting auto-reply..."
        systemctl --user restart outreach-autoreply
        sleep 2
        systemctl --user status outreach-autoreply --no-pager | head -5
        ;;
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
        systemctl --user restart outreach-autoreply
        pkill -f 'run_bot.py' || true
        sleep 5
        echo "--- Status ---"
        systemctl --user is-active outreach-autoreply && echo "auto-reply: UP" || echo "auto-reply: DOWN"
        pgrep -af 'run_bot.py' | grep -v pgrep | wc -l | xargs -I{} echo "persona bots: {} running"
        ;;
    *)
        echo "Usage: rs [autoreply|bots|reminder|all]"
        ;;
esac
