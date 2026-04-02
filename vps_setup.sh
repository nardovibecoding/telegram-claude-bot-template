#!/bin/bash
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
# VPS Setup Script for telegram-claude-bot-template
# Run as root on a fresh Ubuntu 24.04 server
set -e

echo "=== Step 1: System update ==="
apt update && apt upgrade -y

echo "=== Step 2: Install Python + dependencies ==="
apt install -y python3 python3-venv python3-pip git curl

echo "=== Step 3: Create user YOUR_VPS_USER ==="
if ! id YOUR_VPS_USER &>/dev/null; then
    adduser --disabled-password --gecos "" YOUR_VPS_USER
    usermod -aG sudo YOUR_VPS_USER
    # Copy SSH keys from root
    mkdir -p /home/YOUR_VPS_USER/.ssh
    cp /root/.ssh/authorized_keys /home/YOUR_VPS_USER/.ssh/
    chown -R YOUR_VPS_USER:YOUR_VPS_USER /home/YOUR_VPS_USER/.ssh
    chmod 700 /home/YOUR_VPS_USER/.ssh
    chmod 600 /home/YOUR_VPS_USER/.ssh/authorized_keys
    # Allow sudo without password for convenience
    echo "YOUR_VPS_USER ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/YOUR_VPS_USER
fi

echo "=== Step 4: Setup project ==="
PROJECT="/home/YOUR_VPS_USER/telegram-claude-bot-template"
if [ ! -d "$PROJECT" ]; then
    mkdir -p "$PROJECT"
fi

echo "=== Step 5: Setup Python venv ==="
cd "$PROJECT"
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

echo "=== Step 6: Install Playwright deps ==="
apt install -y libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxdamage1 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2t64 libxshmfence1 2>/dev/null || true

echo "=== Step 7: Setup systemd service ==="
cat > /etc/systemd/system/telegram-bots.service <<'EOF'
[Unit]
Description=Telegram Bots
After=network.target

[Service]
User=YOUR_VPS_USER
WorkingDirectory=/home/YOUR_VPS_USER/telegram-claude-bot-template
ExecStart=/bin/bash start_all.sh
Restart=always
RestartSec=10
Environment=PATH=/home/YOUR_VPS_USER/telegram-claude-bot-template/venv/bin:/usr/local/bin:/usr/bin:/bin
StandardOutput=append:/tmp/start_all.log
StandardError=append:/tmp/start_all.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable telegram-bots

echo "=== Step 8: Install Claude CLI ==="
if [ ! -f /home/YOUR_VPS_USER/.local/bin/claude ]; then
    su - YOUR_VPS_USER -c 'curl -fsSL https://claude.ai/install.sh | sh'
fi

echo "=== Step 9: Fix ownership ==="
chown -R YOUR_VPS_USER:YOUR_VPS_USER "$PROJECT"
chown -R YOUR_VPS_USER:YOUR_VPS_USER /home/YOUR_VPS_USER/.local 2>/dev/null || true

echo ""
echo "========================================="
echo "  VPS Setup Complete!"
echo "========================================="
echo ""
echo "Remaining manual steps:"
echo "  1. Upload project files (rsync from Mac)"
echo "  2. Install Python packages: cd ~/telegram-claude-bot-template && source venv/bin/activate && pip install -r requirements.txt"
echo "  3. Install Playwright: playwright install chromium"
echo "  4. Login to Claude: claude login"
echo "  5. Start bots: sudo systemctl start telegram-bots"
echo ""
