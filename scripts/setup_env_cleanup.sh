#!/bin/bash
# Remove unused Twitter/X credentials from VPS .env
# Run: ssh vps "bash ~/telegram-claude-bot/scripts/setup_env_cleanup.sh"
set -euo pipefail

ENV_FILE="$HOME/telegram-claude-bot/.env"
BACKUP="$HOME/telegram-claude-bot/.env.bak.$(date +%s)"

cp "$ENV_FILE" "$BACKUP"
echo "Backup: $BACKUP"

# Remove Twitter/X credential lines
grep -v '^TWITTER_USERNAME=\|^TWITTER_EMAIL=\|^TWITTER_PASSWORD=\|^X_API_KEY=\|^X_API_SECRET=\|^X_ACCESS_TOKEN=\|^X_ACCESS_TOKEN_SECRET=\|^X_CLIENT_ID=\|^X_CLIENT_SECRET=' "$ENV_FILE" > "${ENV_FILE}.tmp"
mv "${ENV_FILE}.tmp" "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo "Removed $(diff <(wc -l < "$BACKUP") <(wc -l < "$ENV_FILE") || true) lines"
echo "Done. Removed TWITTER_* and X_* credentials."
