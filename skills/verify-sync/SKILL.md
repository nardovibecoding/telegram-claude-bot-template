---
name: verify-sync
description: Verify Mac and VPS are in sync (git, memory, cookies, MCP config)
trigger: verify sync, check sync, mac vps sync, are we synced
tags: [sync, verify, mac, vps, git]
---

# Verify Sync

## What syncs between Mac and VPS
1. **Code**: git push/pull (Mac → GitHub → VPS)
2. **Memory**: rsync every 10 min (bidirectional)
3. **Cookies**: twitter_cookies.json (Mac → VPS after refresh)
4. **MCP config**: sync_claude_config.py every 10 min

## Steps

### 1. Check git sync
```bash
# Mac HEAD
git -C ~/telegram-claude-bot log --oneline -1

# VPS HEAD
ssh YOUR_VPS_USER@YOUR_VPS_IP "cd ~/telegram-claude-bot && git log --oneline -1"
```
- Should show same commit hash
- If different: someone pushed without pulling on the other side

### 2. Check memory sync
```bash
# Compare memory file dates
ls -la ~/.claude/projects/[project-dir]/memory/*.md

ssh YOUR_VPS_USER@YOUR_VPS_IP "ls -la ~/.claude/projects/[project-dir]/memory/*.md"
```
- Project dir is derived from working dir path — run `ls ~/.claude/projects/` to find it
- Mac and VPS dirs will differ (different home paths)
- Synced via rsync every 10 min

### 3. Check cookie sync
```bash
# Mac cookie age
stat -f '%m' ~/telegram-claude-bot/twitter_cookies.json && echo "now: $(date +%s)"

# VPS cookie age
ssh YOUR_VPS_USER@YOUR_VPS_IP "stat -c '%Y' ~/telegram-claude-bot/twitter_cookies.json && echo 'now:' && date +%s"
```
- Should be within ~10 min of each other
- Mac refreshes cookies → syncs to VPS

### 4. Check MCP config
```bash
# Mac MCP servers
cat ~/.claude/settings.json | python3 -c "import sys,json; print(sorted(json.load(sys.stdin).get('mcpServers',{}).keys()))"

# VPS MCP servers
ssh YOUR_VPS_USER@YOUR_VPS_IP "cat ~/.claude/settings.json | python3 -c \"import sys,json; print(sorted(json.load(sys.stdin).get('mcpServers',{}).keys()))\""
```
- Same server names should appear on both sides
- Paths will differ (Mac /Users/YOUR_USERNAME vs VPS /home/YOUR_VPS_USER)

### 5. Report differences
Present findings as a table:
```
| Component  | Mac          | VPS          | Status |
|------------|--------------|--------------|--------|
| Git HEAD   | abc1234      | abc1234      | OK     |
| Cookies    | 2h ago       | 2h ago       | OK     |
| Memory     | 5 files      | 5 files      | OK     |
| MCP config | 3 servers    | 3 servers    | OK     |
```

## Fix sync issues
- Git out of sync: `git pull` on the behind side
- Cookies stale: `touch .cookies_need_refresh` on VPS
- Memory missing: wait for next sync (10 min) or run `~/sync_claude_memory.sh` on Mac
- MCP config mismatch: use update-mcp-config skill
