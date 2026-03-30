---
name: add-service
description: Create a systemd user service on VPS
trigger: add service, systemd, daemon, background service
tags: [systemd, service, vps, daemon]
---

# Add Systemd User Service

## Steps

### 1. Create service file
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "mkdir -p ~/.config/systemd/user"
ssh YOUR_VPS_USER@YOUR_VPS_IP "cat > ~/.config/systemd/user/<name>.service << 'EOF'
[Unit]
Description=<Human readable description>
After=network.target

[Service]
Type=simple
WorkingDirectory=~/telegram-claude-bot
ExecStart=~/telegram-claude-bot/venv/bin/python <script.py>
Restart=always
RestartSec=5
Environment=PATH=~/telegram-claude-bot/venv/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
EOF"
```

### 2. Enable and start
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "systemctl --user daemon-reload"
ssh YOUR_VPS_USER@YOUR_VPS_IP "systemctl --user enable <name>"
ssh YOUR_VPS_USER@YOUR_VPS_IP "systemctl --user start <name>"
```

### 3. Verify
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "systemctl --user status <name>"
```

### 4. Check logs
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "journalctl --user -u <name> --no-pager -n 20"
```

## Existing services
- `xhs-mcp` — Xiaohongshu MCP server (port 18060)
- `douyin-mcp` — Douyin MCP server (port 18070)
- `com.YOUR_USER.telegram-bots` — managed via launchd on Mac (NOT systemd)

## Common operations
```bash
# Restart
systemctl --user restart <name>

# Stop
systemctl --user stop <name>

# View logs
journalctl --user -u <name> -f

# Disable auto-start
systemctl --user disable <name>
```

## Rules
- Always use `--user` flag (not root systemd)
- Set `Restart=always` and `RestartSec=5` for production services
- Use full paths in ExecStart (venv python, not system python)
- WorkingDirectory should be the project root
- Test the script manually before creating the service
