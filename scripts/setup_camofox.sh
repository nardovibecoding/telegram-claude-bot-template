#!/bin/bash
# Setup camofox systemd user service
# Run once on VPS: bash ~/telegram-claude-bot/scripts/setup_camofox.sh

cat > ~/.config/systemd/user/camofox.service << 'SVCEOF'
[Unit]
Description=Camofox Browser REST Server
After=network.target

[Service]
Type=simple
WorkingDirectory=~/camofox-browser
Environment=NODE_ENV=production
Environment=CAMOFOX_PORT=19377
Environment=MAX_SESSIONS=15
Environment=MAX_TABS_PER_SESSION=5
ExecStart=/usr/bin/node --max-old-space-size=512 server.js
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
SVCEOF

systemctl --user daemon-reload
systemctl --user enable camofox
systemctl --user restart camofox
sleep 3
systemctl --user is-active camofox
curl -sf http://localhost:19377/health && echo "camofox OK"
