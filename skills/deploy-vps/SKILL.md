---
name: deploy-vps
description: Deploy code changes from Mac to VPS via git push + pull + bot restart
trigger: deploy, push to vps, ship it, send to production
tags: [deploy, vps, git, production]
---

# Deploy to VPS

## Prerequisites
- All changed .py files pass `py_compile` locally
- Working directory: `~/telegram-claude-bot/`
- VPS: `<user>@<vps-ip>`

## Steps

### 1. Pre-flight checks
```bash
# Verify syntax on ALL changed files
for f in $(git diff --name-only --diff-filter=ACMR HEAD | grep '\.py$'); do
  python3 -c "import py_compile; py_compile.compile('$f', doraise=True)"
done
```

### 2. Commit and push
```bash
git add -A
git commit -m "descriptive message"
git push origin main
```

### 3. Pull on VPS
```bash
ssh <user>@<vps-ip> "cd ~/telegram-claude-bot && git fetch && git reset --hard origin/main"
```
- NEVER use `scp` to deploy code. Always git push then pull.
- `git reset --hard` ensures VPS matches remote exactly.

### 4. Restart affected bot
```bash
# For admin_bot changes:
ssh <user>@<vps-ip> "kill \$(pgrep -f 'python admin_bot' | head -1)"

# For persona bot changes:
ssh <user>@<vps-ip> "kill \$(pgrep -f 'run_bot.py <id>' | head -1)"
```
- NEVER manually start bots. `start_all.sh` auto-restarts in 5 seconds.
- NEVER run `ssh -f ... python admin_bot.py` — creates Telegram Conflict errors.
- Wait at least 5 seconds after kill for restart.

### 5. Verify
```bash
# Check process is running
ssh <user>@<vps-ip> "sleep 8 && pgrep -af 'admin_bot\|run_bot'"

# Check logs for errors
ssh <user>@<vps-ip> "tail -20 /tmp/start_all.log"
```

## Common mistakes
- Forgetting to `git pull` on VPS before making manual edits there
- Using `scp` instead of git (bypasses version control)
- Manually starting admin_bot instead of letting start_all.sh restart it
- Not checking /tmp/start_all.log after deploy
- Not running py_compile before pushing
