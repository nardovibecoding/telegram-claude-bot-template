#!/bin/bash
# Health check for Edwin Claude Code bot
# Runs via cron every 5min on VPS
# Checks: tmux session alive, claude process running, OAuth token expiry
# Auto-refreshes OAuth before expiry. Alerts on failure.

CREDS="$HOME/.claude/.credentials.json"
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
    touch /tmp/edwin_needs_attention
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ALERT: $msg" >> /tmp/edwin_health.log
}

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> /tmp/edwin_health.log
}

# ── OAuth auto-refresh ──────────────────────────────────────
refresh_oauth() {
    if [ ! -f "$CREDS" ]; then
        log "No credentials file found"
        return 1
    fi

    local refresh_token
    refresh_token=$(python3 -c "import json; print(json.load(open('$CREDS'))['claudeAiOauth']['refreshToken'])" 2>/dev/null)
    if [ -z "$refresh_token" ]; then
        log "No refresh token found"
        return 1
    fi

    local response
    response=$(curl -s -m 15 -X POST "https://platform.claude.com/v1/oauth/token" \
        -H "Content-Type: application/json" \
        -d "{
            \"grant_type\": \"refresh_token\",
            \"refresh_token\": \"$refresh_token\",
            \"client_id\": \"9d1c250a-e61b-44d9-88ed-5944d1962f5e\",
            \"scope\": \"user:inference user:profile user:sessions:claude_code user:mcp_servers user:file_upload\"
        }" 2>/dev/null)

    local new_access new_refresh expires_in
    new_access=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null)
    new_refresh=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('refresh_token',''))" 2>/dev/null)
    expires_in=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin)['expires_in'])" 2>/dev/null)

    if [ -z "$new_access" ] || [ -z "$expires_in" ]; then
        log "OAuth refresh FAILED: $response"
        return 1
    fi

    # Update credentials file
    python3 -c "
import json, time
with open('$CREDS') as f:
    creds = json.load(f)
oauth = creds['claudeAiOauth']
oauth['accessToken'] = '$new_access'
if '$new_refresh':
    oauth['refreshToken'] = '$new_refresh'
oauth['expiresAt'] = int(time.time() * 1000) + $expires_in * 1000
with open('$CREDS', 'w') as f:
    json.dump(creds, f)
" 2>/dev/null

    if [ $? -eq 0 ]; then
        log "OAuth refreshed OK — expires in ${expires_in}s"
        return 0
    else
        log "OAuth refresh: failed to write credentials"
        return 1
    fi
}

# ── Pre-emptive refresh: if token expires within 10 min ─────
check_and_refresh_oauth() {
    if [ ! -f "$CREDS" ]; then return; fi

    local expires_at now_ms margin_ms
    expires_at=$(python3 -c "import json; print(json.load(open('$CREDS'))['claudeAiOauth']['expiresAt'])" 2>/dev/null)
    now_ms=$(python3 -c "import time; print(int(time.time()*1000))" 2>/dev/null)
    margin_ms=600000  # 10 min

    if [ -n "$expires_at" ] && [ "$((expires_at - now_ms))" -lt "$margin_ms" ]; then
        log "Token expires in <10min — auto-refreshing..."
        if refresh_oauth; then
            send_alert "🔄 *Edwin OAuth auto-refreshed* — no action needed"
            # Clear alert flag since we fixed it
            rm -f "$FLAG" /tmp/edwin_needs_attention
        else
            send_alert "🔑 *Edwin OAuth refresh FAILED* — run \`/login\` manually in tmux"
        fi
    fi
}

# ── Cooldown check ──────────────────────────────────────────
if [ -f "$FLAG" ]; then
    last=$(stat -c %Y "$FLAG" 2>/dev/null || echo 0)
    now=$(date +%s)
    if [ $((now - last)) -lt $COOLDOWN ]; then
        # Still in cooldown, but always try auto-refresh
        check_and_refresh_oauth
        exit 0
    fi
fi

# ── Check 1: tmux session exists ────────────────────────────
if ! tmux has-session -t edwin 2>/dev/null; then
    send_alert "⚠️ *Edwin bot DOWN* — tmux session 'edwin' not found"
    exit 1
fi

# ── Check 2: claude process running ─────────────────────────
if ! pgrep -f 'claude.*channels.*telegram' > /dev/null 2>&1; then
    send_alert "⚠️ *Edwin bot DOWN* — claude process not running"
    exit 1
fi

# ── Check 3: OAuth — auto-refresh before it expires ─────────
check_and_refresh_oauth

# ── Check 4: OAuth errors in tmux output ────────────────────
output=$(tmux capture-pane -t edwin -p -S -20 2>/dev/null)
if echo "$output" | grep -q "OAuth token has expired\|authentication_error\|401"; then
    log "Detected OAuth error in tmux — attempting refresh..."
    if refresh_oauth; then
        send_alert "🔄 *Edwin OAuth auto-refreshed* after 401 — bot should recover"
        rm -f "$FLAG" /tmp/edwin_needs_attention
    else
        send_alert "🔑 *Edwin OAuth refresh FAILED* — run \`/login\` manually in tmux"
    fi
    exit 1
fi

# ── All good ────────────────────────────────────────────────
if [ -f "$FLAG" ]; then
    rm -f "$FLAG"
    log "OK: Edwin healthy, cleared alert flag"
fi
