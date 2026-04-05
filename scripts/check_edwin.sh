#!/bin/bash
# Health check for Edwin Claude Code bot
# Runs via cron every 5min on VPS
# Checks: tmux session alive, claude process running, no OAuth errors
# Alerts: TG DM to admin + touch flag for Mac notification

ADMIN_TOKEN="$(grep TELEGRAM_BOT_TOKEN_ADMIN ~/telegram-claude-bot/.env | cut -d= -f2)"
ADMIN_USER_ID="$(grep ADMIN_USER_ID ~/telegram-claude-bot/.env | cut -d= -f2)"
FLAG="/tmp/edwin_alert_sent"
COOLDOWN=1800  # 30min between alerts

send_alert() {
    local msg="$1"
    curl -s -X POST "https://api.telegram.org/bot${ADMIN_TOKEN}/sendMessage" \
        -d chat_id="$ADMIN_USER_ID" \
        -d text="$msg" \
        -d parse_mode="Markdown" > /dev/null 2>&1
    touch "$FLAG"
    # Touch Mac sync flag
    touch /tmp/edwin_needs_attention
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ALERT: $msg" >> /tmp/edwin_health.log
}

# Cooldown check — don't spam
if [ -f "$FLAG" ]; then
    last=$(stat -c %Y "$FLAG" 2>/dev/null || echo 0)
    now=$(date +%s)
    if [ $((now - last)) -lt $COOLDOWN ]; then
        exit 0
    fi
fi

# Check 1: tmux session exists
if ! tmux has-session -t edwin 2>/dev/null; then
    send_alert "⚠️ *Edwin bot DOWN* — tmux session 'edwin' not found"
    exit 1
fi

# Check 2: claude process running
if ! pgrep -f 'claude.*channels.*telegram' > /dev/null 2>&1; then
    send_alert "⚠️ *Edwin bot DOWN* — claude process not running"
    exit 1
fi

# Check 3: OAuth errors (check last 20 lines of tmux output)
output=$(tmux capture-pane -t edwin -p -S -20 2>/dev/null)
if echo "$output" | grep -q "OAuth token has expired\|authentication_error\|401"; then
    send_alert "🔑 *Edwin bot OAuth expired* — run \`/login\` in tmux session"
    exit 1
fi

# All good — clear flag if it exists
if [ -f "$FLAG" ]; then
    rm -f "$FLAG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] OK: Edwin healthy, cleared alert flag" >> /tmp/edwin_health.log
fi
