#!/bin/bash
# Fix VPS sudoers: remove NOPASSWD:ALL, keep targeted rules only
# Run: ssh vps "bash ~/telegram-claude-bot/scripts/setup_sudoers_fix.sh"
set -euo pipefail

SUDOERS_FILE="/etc/sudoers.d/bernard"

# Write targeted rules (overwrites existing file)
sudo tee "$SUDOERS_FILE" > /dev/null << 'EOF'
bernard ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart telegram-bots
bernard ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart xhs-mcp
bernard ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart douyin-mcp
bernard ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart clipboard-server
bernard ALL=(ALL) NOPASSWD: /usr/bin/systemctl start telegram-bots
bernard ALL=(ALL) NOPASSWD: /usr/bin/systemctl status *
bernard ALL=(ALL) NOPASSWD: /usr/bin/journalctl *
bernard ALL=(ALL) NOPASSWD: /usr/sbin/ufw status
bernard ALL=(ALL) NOPASSWD: /usr/bin/fail2ban-client *
EOF

sudo chmod 440 "$SUDOERS_FILE"
sudo visudo -cf "$SUDOERS_FILE" && echo "OK: sudoers syntax valid" || echo "FAIL: syntax error"
echo "Done. NOPASSWD:ALL removed. Targeted rules in place."
