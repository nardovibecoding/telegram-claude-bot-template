#!/bin/bash
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
# Extract knowledge from expiring sessions, then clean up
# Improved: head+tail extraction, specific prompt, large file chunking
cd ~/telegram-claude-bot
source venv/bin/activate
source .env 2>/dev/null

# Find files about to be deleted
OLD_FILES=$(find ~/.claude/projects/ -name "*.jsonl" -mtime +7 2>/dev/null)
if [ -z "$OLD_FILES" ]; then
    echo "[$(date)] No old JSONL files to clean"
    exit 0
fi

FILE_COUNT=$(echo "$OLD_FILES" | wc -l | tr -d ' ')
echo "[$(date)] Found $FILE_COUNT old JSONL files to process"

# Read current memory index for cross-check
MEMORY_INDEX=""
if [ -f ~/telegram-claude-bot-template/memory/MEMORY.md ]; then
    MEMORY_INDEX=$(cat ~/telegram-claude-bot-template/memory/MEMORY.md)
fi

SUMMARY="/tmp/jsonl_extract_$(date +%Y%m%d).txt"
> "$SUMMARY"  # Clear file

for f in $OLD_FILES; do
    FNAME=$(basename "$f")
    FSIZE=$(du -h "$f" | cut -f1)
    echo "=== $FNAME ($FSIZE) ===" >> "$SUMMARY"
    
    # Head: first 100 lines — captures task description and initial context
    head -100 "$f" | grep -oP '"text":"[^"]*"' | head -30 >> "$SUMMARY"
    
    echo "--- middle (skipped) ---" >> "$SUMMARY"
    
    # Tail: last 300 lines — captures conclusions and results
    tail -300 "$f" | grep -oP '"text":"[^"]*"' | head -50 >> "$SUMMARY"
    
    echo "" >> "$SUMMARY"
done

# Cap summary at 50KB to avoid token limits
if [ -s "$SUMMARY" ]; then
    SSIZE=$(wc -c < "$SUMMARY")
    if [ "$SSIZE" -gt 50000 ]; then
        head -c 50000 "$SUMMARY" > "$SUMMARY.tmp" && mv "$SUMMARY.tmp" "$SUMMARY"
        echo "[truncated to 50KB]" >> "$SUMMARY"
    fi
fi

# Only run extraction if summary has content
if [ -s "$SUMMARY" ]; then
    CLAUDE_BIN="${HOME}/.claude/local/claude"
    if [ ! -f "$CLAUDE_BIN" ]; then
        for path in /usr/local/bin/claude "$HOME/.local/bin/claude"; do
            if [ -f "$path" ]; then
                CLAUDE_BIN="$path"
                break
            fi
        done
    fi

    if [ -f "$CLAUDE_BIN" ]; then
        echo "[$(date)] Running extraction with Claude Haiku..."
        timeout 300 "$CLAUDE_BIN" -p --model claude-haiku-4-5-20251001 \
            --dangerously-skip-permissions \
            "You are a memory extractor. Read the session excerpts in $SUMMARY.

EXISTING MEMORY INDEX (do NOT duplicate these):
$MEMORY_INDEX

EXTRACT these specific types of information ONLY if NOT already in memory:
1. Architecture decisions (\"we decided to...\", \"changed X to Y because...\")
2. New features/systems built (new files, new cron jobs, new commands)
3. Bug root causes (\"the bug was caused by...\", \"root cause:\")
4. User corrections/preferences (\"don't do X\", \"always do Y\")
5. External service changes (API keys, endpoints, rate limits, breakages)
6. Cron schedule changes
7. Config/deployment changes

For each finding:
- Cross-check against ~/telegram-claude-bot-template/memory/ files
- Skip if already documented
- Save genuinely new findings to appropriate memory files
- Use standard frontmatter format (name, description, type)
- Update MEMORY.md index if new files created
- git add memory/ && git commit -m 'memory: auto-extract from expiring sessions' && git push origin main

If nothing new found, just say 'nothing new' and do NOT commit." \
            2>/dev/null
        echo "[$(date)] Extraction complete"
    else
        echo "[$(date)] WARNING: Claude CLI not found at $CLAUDE_BIN"
    fi
    rm -f "$SUMMARY"
fi

# Now delete the old files
DELETED=$(find ~/.claude/projects/ -name "*.jsonl" -mtime +7 -delete -print 2>/dev/null | wc -l | tr -d ' ')
echo "[$(date)] Cleanup done: deleted $DELETED files"

# --- Part 2: Stale memory check (weekly, only on Sundays) ---
DOW=$(date +%u)  # 1=Mon, 7=Sun
if [ "$DOW" = "7" ]; then
    echo "[$(date)] Running weekly stale memory check..."
    STALE_PROMPT="Read all memory files in ~/telegram-claude-bot-template/memory/. Then check:
1) Cron schedules: run crontab -l and compare against any schedule mentioned in memory files
2) File paths: check if referenced files/scripts still exist
3) Process references: check if mentioned services/bots are still in start_all.sh or systemd
For each mismatch, update the memory file to match reality.
If nothing stale, just say all memory current.
Git commit with message memory: weekly stale check auto-fix and push if changes made."

    if [ -f "$CLAUDE_BIN" ]; then
        timeout 300 "$CLAUDE_BIN" -p --model claude-haiku-4-5-20251001 \
            --dangerously-skip-permissions \
            "$STALE_PROMPT" \
            2>/dev/null
        echo "[$(date)] Stale memory check complete"
    fi
fi
