#!/bin/bash
# Copyright (c) 2026 Nardo (<github-user>). AGPL-3.0 — see LICENSE
# Add tweet_idea_cron.py to VPS crontab.
# Run once: ssh vps "bash ~/telegram-claude-bot/scripts/setup_tweet_idea_cron.sh"

PROJ="$HOME/telegram-claude-bot"
VENV="$PROJ/venv/bin/python"
SCRIPT="$PROJ/tweet_idea_cron.py"
LOG="/tmp/tweet_idea_cron.log"

# Add two cron entries: 13:30 HKT (05:30 UTC) daily + every 2h after that
CRON1="30 5 * * * cd $PROJ && $VENV $SCRIPT >> $LOG 2>&1"
CRON2="30 7,9,11,13,15 * * * cd $PROJ && $VENV $SCRIPT >> $LOG 2>&1"

(crontab -l 2>/dev/null | grep -v "tweet_idea_cron"; echo "$CRON1"; echo "$CRON2") | crontab -

echo "Tweet idea cron installed:"
crontab -l | grep tweet_idea_cron
