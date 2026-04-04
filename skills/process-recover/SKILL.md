---
name: process-recover
description: Recover crashed or stuck bot processes
trigger: bot stuck, process missing, restart bot, recover
tags: [process, recovery, restart, crash]
---

# Process Recovery

## Steps

### 1. Check what's running
```bash
ssh <user>@<vps-ip> "pgrep -af 'run_bot\|admin_bot'"
```
Expected: 8 persona bots (daliu, sbf, devasini, twitter, xcn, xai, xniche, reddit) + 1 admin_bot = 9 processes

### 2. If a specific bot is missing
```bash
# Check logs for crash reason
ssh <user>@<vps-ip> "grep '<bot_name>' /tmp/start_all.log | tail -20"
```
- `start_all.sh` auto-restarts crashed bots in 5 seconds
- If the bot keeps crashing, the log will show repeated "Starting..." + error

### 3. If a bot is stuck (running but not responding)
```bash
# Kill the stuck process
ssh <user>@<vps-ip> "kill \$(pgrep -f 'run_bot.py <id>' | head -1)"
# start_all.sh restarts it in 5s
```

### 4. For admin_bot specifically
```bash
# NEVER manually start admin_bot — creates Conflict errors
ssh <user>@<vps-ip> "kill \$(pgrep -f 'python admin_bot' | head -1)"
# Wait for start_all.sh to restart (5s)
```

### 5. If start_all.sh itself is down
```bash
ssh <user>@<vps-ip> "pgrep -af start_all"
# If not running:
ssh <user>@<vps-ip> "cd ~/telegram-claude-bot && nohup ./start_all.sh >> /tmp/start_all.log 2>&1 &"
```

### 6. Nuclear option (restart everything)
```bash
ssh <user>@<vps-ip> "cd ~/telegram-claude-bot && ./start_all.sh stop && sleep 10 && nohup ./start_all.sh >> /tmp/start_all.log 2>&1 &"
```
- Wait 10-30s between stop and start to avoid Telegram Conflict errors

### 7. Verify recovery
```bash
ssh <user>@<vps-ip> "sleep 10 && pgrep -af 'run_bot\|admin_bot' && echo '---' && tail -5 /tmp/start_all.log"
```

## Rules
- NEVER manually start individual bots — let start_all.sh handle it
- NEVER kill start_all.sh itself unless doing a full restart
- Always check /tmp/start_all.log FIRST to understand why a bot crashed
- Wait 10-30s between kill/start to avoid Telegram Conflict errors
