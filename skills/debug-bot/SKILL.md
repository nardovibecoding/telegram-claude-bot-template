---
name: debug-bot
description: Diagnose and fix bot errors using start_all.log
trigger: bot down, bot error, bot crash, bot not responding, debug
tags: [debug, logs, error, troubleshooting]
---

# Debug Bot Issues

## CRITICAL: Always read the RIGHT log
- ALL bot output goes to `/tmp/start_all.log`
- NEVER read `/tmp/admin_bot.log` or `/tmp/<bot>.log` — those are STALE
- This rule exists because we wasted 30+ minutes TWICE by not checking the right log.

## Steps

### 1. Read the log FIRST
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "tail -50 /tmp/start_all.log"
```

### 2. Filter for errors
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "grep 'ERROR\|WARNING\|Traceback\|Exception' /tmp/start_all.log | tail -30"
```

### 3. Check process status
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "pgrep -af 'run_bot\|admin_bot'"
```
- All 8 persona bots + 1 admin bot should be running (9 total)
- Missing process = crashed, start_all.sh should auto-restart in 5s

### 4. Identify the pattern
- **Traceback with file:line** — go directly to that source file and fix
- **Telegram Conflict error** — two bot instances running, kill duplicates
- **API timeout/connection error** — external service issue, add retry logic
- **ImportError/ModuleNotFoundError** — missing dependency, install in venv
- **UnboundLocalError** — variable used before assignment, fix the code

### 5. Fix root cause
- NEVER apply band-aids (retry loops, try/except that swallows errors)
- Trace the full execution path
- If fails 2+ times: STOP. Read the FULL error. Root cause only.

### 6. Verify fix
```bash
# Syntax check
python3 -c "import py_compile; py_compile.compile('file.py', doraise=True)"

# Deploy and restart (use deploy-vps skill)
# Then verify:
ssh YOUR_VPS_USER@YOUR_VPS_IP "sleep 8 && tail -10 /tmp/start_all.log"
```

## Common error patterns
| Error | Cause | Fix |
|-------|-------|-----|
| Conflict: terminated by other getUpdates | Duplicate bot instance | Kill all, let start_all.sh restart |
| insufficient_balance | MiniMax plan limit | Only M2.5 models work |
| Connection reset by peer | Network blip | Add retry with backoff |
| JSONDecodeError | MiniMax `<think>` blocks | Strip think tags before parsing |
