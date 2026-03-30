#!/bin/bash
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
# Network monitor — alert on unexpected outbound connections from bot processes
# Runs via cron every 10 minutes on VPS only.
# Sends Telegram alert if unknown hosts detected.

set -euo pipefail

# --- Config ---
BOT_TOKEN="${TELEGRAM_BOT_TOKEN_ADMIN:-}"
ADMIN_CHAT_ID="${ADMIN_USER_ID:-}"
LOG_FILE="/tmp/network_monitor.log"
MAX_LOG_SIZE=1048576  # 1MB

# Load .env if token not set
if [ -z "$BOT_TOKEN" ] || [ -z "$ADMIN_CHAT_ID" ]; then
    ENV_FILE="$(dirname "$0")/../.env"
    if [ -f "$ENV_FILE" ]; then
        export $(grep -E '^(TELEGRAM_BOT_TOKEN_ADMIN|ADMIN_USER_ID)=' "$ENV_FILE" | xargs)
        BOT_TOKEN="${TELEGRAM_BOT_TOKEN_ADMIN:-}"
        ADMIN_CHAT_ID="${ADMIN_USER_ID:-}"
    fi
fi

if [ -z "$BOT_TOKEN" ] || [ -z "$ADMIN_CHAT_ID" ]; then
    echo "$(date): Missing TELEGRAM_BOT_TOKEN_ADMIN or ADMIN_USER_ID" >> "$LOG_FILE"
    exit 1
fi

# Whitelist: known-good destination hosts/IPs and patterns
# Matching: substring match on both resolved hostname and raw IP
WHITELIST=(
    # --- Infrastructure ---
    "localhost"
    "127.0.0.1"
    "::1"

    # --- CDN / Cloud (covers most news sites behind CDN) ---
    # Cloudflare (104.16-31.x.x, 172.64-71.x.x)
    "cloudflare"
    "104.16." "104.17." "104.18." "104.19." "104.20." "104.21." "104.22." "104.23."
    "104.24." "104.25." "104.26." "104.27." "104.28." "104.29." "104.30." "104.31."
    "172.64." "172.65." "172.66." "172.67." "172.68." "172.69." "172.70." "172.71."
    "2606:4700:"    # Cloudflare IPv6
    "2a06:98c"      # Cloudflare IPv6 (covers 98c0-98cf)
    # Akamai
    "akamaitechnologies.com" "akamaiedge.net" "akamai.net"
    "2.16." "2.17." "2.18." "2.19." "2.20." "2.21." "2.22." "2.23."
    "23.0." "23.1." "23.2." "23.3." "23.4." "23.5." "23.6." "23.7."
    "2a02:26f0:"    # Akamai IPv6
    "2a00:1288:"    # Akamai IPv6
    # Fastly
    "fastly" "199.232."
    "2a04:4e42:"    # Fastly IPv6
    # AWS CloudFront
    "cloudfront.net"
    "2600:9000:"    # CloudFront IPv6
    # Google Cloud / CDN
    "1e100.net" "google.com" "googleapis.com" "gstatic.com"
    "142.250." "142.251." "172.217." "216.58." "74.125."
    "2a00:1450:"    # Google IPv6
    "2600:1901:"    # Google Cloud IPv6 (Anthropic API)
    # Microsoft / Azure
    "msedge.net" "azure" "microsoft.com"
    # DigitalOcean / YOUR_VPS_PROVIDER
    "digitalocean.com"

    # --- Telegram ---
    "api.telegram.org"
    "149.154."
    "2001:67c:4e8:"

    # --- AI APIs ---
    "api.minimaxi.com"
    "xaminim.com"
    "47.79."
    "api.anthropic.com"
    "anthropic.com"
    "sentry.io"
    "statsig.anthropic.com"
    # Anthropic runs on GCP — resolves to 34.x.x.x / googleusercontent.com
    "googleusercontent.com"
    "34.149." "34.160." "34.102." "34.96." "34.98." "34.104." "34.105."
    "34.110." "34.111." "34.116." "34.117." "34.118." "34.120." "34.121."
    "34.122." "34.123." "34.124." "34.125." "34.126." "34.127." "34.128."

    # --- Twitter/X ---
    "api.twitter.com" "twitter.com" "x.com" "api.x.com"
    "abs.twimg.com" "pbs.twimg.com" "twimg.com"
    "nitter"

    # --- Reddit ---
    "reddit.com" "www.reddit.com" "old.reddit.com" "redd.it"

    # --- News RSS / scraping ---
    "bbc.co.uk" "bbci.co.uk"
    "cnn.com" "rss.cnn.com"
    "cnbc.com"
    "ft.com"
    "theguardian.com" "guardian.co.uk"
    "reuters.com"
    "bloomberg.com"
    "scmp.com"
    "hk01.com"
    "mingpao.com"
    "yahoo.com"
    "asiatimes.com"
    "chinadigitaltimes.net"
    "techcrunch.com"
    "venturebeat.com"
    "thenextweb.com"
    "indiehackers.com"
    "producthunt.com"
    "wordpress.com" "wp.com"    # news sites on WP hosting
    "automattic.com"            # WordPress/Automattic IPs (192.0.78.x)
    "192.0.78."                 # Automattic IP range

    # --- Crypto news ---
    "coindesk.com"
    "theblock.co"
    "cryptonews.com"
    "cryptoslate.com"
    "decrypt.co"
    "protos.com"

    # --- Crypto data ---
    "coinmarketcap.com"
    "llama.fi" "defillama.com"

    # --- China sources ---
    "weibo.com"
    "zhihu.com"
    "bilibili.com"
    "douyin.com"
    "36kr.com"
    "xiaohongshu.com"

    # --- Hacker News / GitHub ---
    "firebaseio.com" "firebase.googleapis.com"
    "github.com" "api.github.com"
    "githubusercontent.com"
    "skillsmp.com"

    # --- DNS (public DNS servers — Cloudflare + Google) ---
    "1.1.1.1" "1.0.0.1"
    "8.8.8.8" "8.8.4.4"

    # --- Package managers ---
    "pypi.org"
    "files.pythonhosted.org"

    # --- MCP ---
    "edamam-food-nutrition-api.p.rapidapi.com"
)

# Rotate log if too large
if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || stat -f%z "$LOG_FILE" 2>/dev/null)" -gt "$MAX_LOG_SIZE" ]; then
    tail -100 "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
fi

# Get bot PIDs
BOT_PIDS=$(pgrep -f 'python.*(run_bot|admin_bot|bot_base|send_digest|x_curator|reddit_digest|news|crypto_news)' 2>/dev/null || true)

if [ -z "$BOT_PIDS" ]; then
    echo "$(date): No bot processes found" >> "$LOG_FILE"
    exit 0
fi

# Collect all outbound connections from bot processes
UNKNOWN_HOSTS=""

for pid in $BOT_PIDS; do
    # Get connections for this PID using ss
    CONNECTIONS=$(ss -tnp 2>/dev/null | grep "pid=$pid," | awk '{print $5}' | sed 's/:[0-9]*$//' | sort -u || true)

    for dest in $CONNECTIONS; do
        # Skip empty, local, link-local
        [ -z "$dest" ] && continue
        [[ "$dest" == "0.0.0.0" ]] && continue
        [[ "$dest" == "::" ]] && continue
        [[ "$dest" == "10."* ]] && continue
        [[ "$dest" == "172.16."* || "$dest" == "172.17."* || "$dest" == "172.18."* || "$dest" == "172.19."* ]] && continue
        [[ "$dest" == "172.20."* || "$dest" == "172.21."* || "$dest" == "172.22."* || "$dest" == "172.23."* ]] && continue
        [[ "$dest" == "172.24."* || "$dest" == "172.25."* || "$dest" == "172.26."* || "$dest" == "172.27."* ]] && continue
        [[ "$dest" == "172.28."* || "$dest" == "172.29."* || "$dest" == "172.30."* || "$dest" == "172.31."* ]] && continue
        [[ "$dest" == "192.168."* ]] && continue
        [[ "$dest" == "fe80:"* ]] && continue

        # Resolve IP to hostname for matching
        HOSTNAME=$(dig +short -x "$dest" 2>/dev/null | head -1 | sed 's/\.$//' || echo "$dest")
        [ -z "$HOSTNAME" ] && HOSTNAME="$dest"

        # Check against whitelist
        IS_KNOWN=false
        for allowed in "${WHITELIST[@]}"; do
            if [[ "$HOSTNAME" == *"$allowed"* ]] || [[ "$dest" == "$allowed" ]]; then
                IS_KNOWN=true
                break
            fi
        done

        # Also resolve whitelist entries and compare IPs
        if [ "$IS_KNOWN" = false ]; then
            for allowed in "${WHITELIST[@]}"; do
                RESOLVED=$(dig +short "$allowed" 2>/dev/null | head -1 || true)
                if [ "$dest" = "$RESOLVED" ]; then
                    IS_KNOWN=true
                    break
                fi
            done
        fi

        if [ "$IS_KNOWN" = false ]; then
            PROC_NAME=$(cat /proc/$pid/comm 2>/dev/null || echo "unknown")
            UNKNOWN_HOSTS="${UNKNOWN_HOSTS}\n- PID $pid ($PROC_NAME) -> $dest ($HOSTNAME)"
        fi
    done
done

if [ -n "$UNKNOWN_HOSTS" ]; then
    MSG="🔴 SECURITY: Unknown outbound connections detected:${UNKNOWN_HOSTS}"
    echo "$(date): ALERT $MSG" >> "$LOG_FILE"

    # Send Telegram alert → Healer/Heartbeat thread (152)
    HEALER_CHAT_ID="${PERSONAL_GROUP_ID}"
    HEALER_THREAD_ID="152"
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -d chat_id="${HEALER_CHAT_ID}" \
        -d message_thread_id="${HEALER_THREAD_ID}" \
        -d text="${MSG}" \
        -d parse_mode="HTML" \
        > /dev/null 2>&1
else
    echo "$(date): OK — all connections whitelisted" >> "$LOG_FILE"
fi
