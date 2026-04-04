#!/bin/bash
# Add ClaudeGPT ProMax AI/tech digest to crontab at 12:30 HKT (04:30 UTC)
set -e

CRON_LINE='30 4 * * * cd ~/telegram-claude-bot && source venv/bin/activate && source .env && python send_ai_tech_digest.py >> /tmp/ai_tech_digest.log 2>&1'
COMMENT='# 12:30 HKT (04:30 UTC) — ClaudeGPT ProMax AI/tech digest'

# Check if already added
if crontab -l 2>/dev/null | grep -q 'send_ai_tech_digest'; then
    echo "Already in crontab"
    exit 0
fi

# Add after evolution feed line
crontab -l 2>/dev/null | sed "/evolution_feed.py/a\\
$COMMENT\\
$CRON_LINE" | crontab -

echo "Added to crontab:"
crontab -l | grep -A1 'ClaudeGPT'
