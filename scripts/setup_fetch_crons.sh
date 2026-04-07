#!/bin/bash
# Setup systemd timers for fetch_cron.py on VPS
set -e

PROJECT_DIR="$HOME/telegram-claude-bot"
PYTHON="$PROJECT_DIR/venv/bin/python"
if [ ! -f "$PYTHON" ]; then
    PYTHON="python3"
fi

# --- fetch-fast: every 1 hour (Twitter + Reddit) ---
cat > /etc/systemd/system/fetch-fast.service << UNIT
[Unit]
Description=Fetch fast sources (Twitter, Reddit)
After=network.target

[Service]
Type=oneshot
User=bernard
WorkingDirectory=$PROJECT_DIR
ExecStart=$PYTHON fetch_cron.py --sources reddit
Environment=HOME=~/
StandardOutput=journal
StandardError=journal
TimeoutStartSec=120
UNIT

cat > /etc/systemd/system/fetch-fast.timer << UNIT
[Unit]
Description=Run fetch-fast every 1 hour

[Timer]
OnCalendar=*:05:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT

# --- fetch-slow: every 3 hours (HN, Dev.to, Lobsters, arXiv, HF) ---
cat > /etc/systemd/system/fetch-slow.service << UNIT
[Unit]
Description=Fetch slow sources (HN, Dev.to, Lobsters, arXiv, HF)
After=network.target

[Service]
Type=oneshot
User=bernard
WorkingDirectory=$PROJECT_DIR
ExecStart=$PYTHON fetch_cron.py --sources hn,devto,lobsters,arxiv,hf
Environment=HOME=~/
StandardOutput=journal
StandardError=journal
TimeoutStartSec=120
UNIT

cat > /etc/systemd/system/fetch-slow.timer << UNIT
[Unit]
Description=Run fetch-slow every 3 hours

[Timer]
OnCalendar=*-*-* 0/3:15:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT

# Enable and start timers
systemctl daemon-reload
systemctl enable --now fetch-fast.timer
systemctl enable --now fetch-slow.timer

echo "--- Timer status ---"
systemctl list-timers fetch-fast.timer fetch-slow.timer --no-pager

echo "--- Done ---"
